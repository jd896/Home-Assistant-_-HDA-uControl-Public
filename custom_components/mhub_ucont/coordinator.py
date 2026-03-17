"""
Coordinator for MHUB uControl integration.

This coordinator is responsible for:
1. Polling the MHUB API at regular intervals
2. Building device structures (zones, IR devices, sources)
3. Caching static data to minimize network traffic
4. Providing data to all platform entities (button.py, number.py, sensor.py, switch.py)

Data Flow:
  MHUB API → Coordinator → Platform Entities → Home Assistant UI
"""
import asyncio
import logging
from datetime import timedelta

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class MhubCoordinator(DataUpdateCoordinator):
    """
    MHUB coordinator with rate limiting and caching.
    
    Polls the MHUB every 5 seconds for zone state changes.
    Caches static configuration data (IR packs, zone config) to reduce network traffic.
    """

    def __init__(self, hass, api):
        """
        Initialize coordinator.
        
        Args:
            hass: Home Assistant instance
            api: MhubApi instance (from api.py) - handles all HTTP requests to MHUB
        """
        super().__init__(
            hass,
            logger=_LOGGER,
            name="mhub_ucont",
            update_interval=timedelta(seconds=5),  # Poll interval - adjust for performance
        )
        self.api = api  # Reference to api.py - all HTTP calls go through this
        
        # ========================================
        # CACHING SYSTEM
        # ========================================
        # Cache for static data that rarely/never changes
        # Keys: "sources", "zones", "device_info", "ir_devices_full"
        self._static_cache = {}
        self._cache_timestamp = {}
        
        # ========================================
        # RATE LIMITING
        # ========================================
        # Prevent excessive polling during startup or errors
        self._last_poll_time = 0
        self._min_poll_interval = 2  # Minimum seconds between polls
        
        # ========================================
        # MONITORING
        # ========================================
        # Track API calls for network traffic analysis
        self._api_call_count = 0
        self._last_count_reset = 0

    async def _async_update_data(self):
        """
        Main data fetch method - called every 5 seconds by Home Assistant.
        
        Returns dict with:
            - zones: List of zone configs with current state
            - sources: Dict mapping input IDs to labels
            - zones_state: Zone state data (compatibility with existing platforms)
            - ir_devices: Dict of IR pack configurations
            - stacked: Boolean stacking status
            - output_to_zone: Dict mapping outputs to zones (for device labeling)
            - ir_port_to_output: Dict mapping IR ports to output IDs (for Pronto passthrough)
            - device_info: MHUB hardware/firmware info
            
        This data is consumed by:
            - button.py: Creates input switch buttons and IR command buttons
            - number.py: Creates volume controls
            - sensor.py: Creates active source sensors
            - switch.py: Creates mute switches
        """
        
        # ========================================
        # RATE LIMITING CHECK
        # ========================================
        import time
        current_time = time.time()
        time_since_last_poll = current_time - self._last_poll_time
        
        if time_since_last_poll < self._min_poll_interval:
            _LOGGER.debug(
                "Skipping poll - only %.1fs since last poll (min: %ds)",
                time_since_last_poll, self._min_poll_interval
            )
            # Return cached data if available
            if hasattr(self, 'data') and self.data:
                return self.data
        
        self._last_poll_time = current_time
        
        # ========================================
        # API CALL MONITORING
        # ========================================
        # Reset counter every minute and log statistics
        if current_time - self._last_count_reset > 60:
            if self._api_call_count > 0:
                _LOGGER.info(
                    "API calls in last minute: %d (avg: %.1f/sec)",
                    self._api_call_count,
                    self._api_call_count / 60
                )
            self._api_call_count = 0
            self._last_count_reset = current_time
        
        try:
            # ========================================
            # 1. FETCH SYSTEM INFO
            # ========================================
            # API Call: GET /api/data/100/
            # Purpose: Get MHUB model, firmware, IR port configuration
            # Called: Every poll (required for stacking detection and IR port info)
            # Used by: api.py → get_system_info()
            self._api_call_count += 1
            info = await self.api.get_system_info()
            data = info.get("data", {})

            # Detect if system is in stacked mode (multiple MHUBs connected)
            stack_info = data.get("stack", {})
            stacked = stack_info.get("stack_status", False)
            
            if stacked:
                _LOGGER.debug("System detected in stacked mode")
            else:
                _LOGGER.debug("System detected in standalone mode")

            # ========================================
            # 2. BUILD SOURCE LIST
            # ========================================
            # Purpose: Map input IDs (1, 2, 3...) to labels ("Sky Q", "Apple TV"...)
            # Cached: 5 minutes (labels rarely change)
            # Used by: button.py (creates input switch buttons with labels)
            sources = await self._get_cached_sources(data)

            # ========================================
            # 3. GET ZONE CONFIGURATION
            # ========================================
            # API Call: GET /api/data/102/ (if not cached)
            # Purpose: Get zone names, output assignments
            # Cached: 5 minutes (zone config rarely changes)
            # Used by: All platforms (number.py, switch.py, sensor.py, button.py)
            zones = await self._get_cached_zones()
            
            _LOGGER.debug("Found %d zones", len(zones))

            # ========================================
            # 4. GET CURRENT ZONE STATE
            # ========================================
            # API Call: GET /api/data/101/ or /api/data/203/ (stacked)
            # Purpose: Get current volume, mute, active input for each zone
            # Cached: Never (real-time data - always fetch)
            # Used by: number.py (volume), switch.py (mute), sensor.py (active source)
            self._api_call_count += 1
            state = await self.api.get_state(stacked)
            zones_state = state.get("data", {}).get("zones", [])

            # ========================================
            # 5. BUILD IR DEVICE MAP
            # ========================================
            # Purpose: Load IR pack configurations and create port mappings
            # This section handles:
            #   - IR pack loading (uControl button definitions)
            #   - IR port to output mapping (for Pronto IR passthrough)
            #   - Output to zone mapping (for device labeling)
            # Used by: button.py (IR buttons), __init__.py (Pronto IR service)
            ir_devices, ir_port_to_output = await self._get_ir_devices(data, stacked)

            # ========================================
            # 6. BUILD OUTPUT-TO-ZONE MAP
            # ========================================
            # Purpose: Map output IDs (a, b, c, d) to zone IDs (z1, z2...)
            # Used by: button.py (labels IR devices with zone names)
            output_to_zone = {}
            for zone in zones:
                zone_id = zone.get("zone_id")
                outputs = zone.get("outputs", [])
                for output in outputs:
                    output_id = output.get("output_id", "").lower()
                    if output_id and zone_id:
                        output_to_zone[output_id] = zone_id
                        _LOGGER.debug(
                            "Mapped output %s to zone %s",
                            output_id, zone_id
                        )

            _LOGGER.debug("Mapped %d outputs to zones: %s", len(output_to_zone), output_to_zone)
            _LOGGER.debug("Mapped %d IR ports to outputs: %s", len(ir_port_to_output), ir_port_to_output)

            # ========================================
            # 7. GET DEVICE INFO
            # ========================================
            # Purpose: MHUB hardware/firmware details for device registry
            # Cached: Permanently (never changes)
            # Used by: All platforms for device_info
            device_info = await self._get_cached_device_info(data)

            # ========================================
            # RETURN COMPLETE DATA STRUCTURE
            # ========================================
            # Note: Maintain compatibility with existing platform files
            # This matches the exact structure from your original coordinator
            return {
                "zones": zones,
                "sources": sources,
                "zones_state": zones_state,
                "ir_devices": ir_devices,
                "stacked": stacked,
                "output_to_zone": output_to_zone,
                "ir_port_to_output": ir_port_to_output,
                "device_info": device_info,
            }
            
        except Exception as e:
            _LOGGER.error("Error updating MHUB data: %s", str(e))
            raise

    # ========================================
    # CACHING METHODS
    # ========================================
    # These methods implement smart caching to reduce API calls
    # Static data is cached for minutes/permanently
    # Dynamic data (zone state) is never cached

    async def _get_cached_sources(self, data):
        """
        Get input source labels with caching.
        
        Purpose: Build map of input IDs to labels (e.g., {1: "Sky Q", 2: "Apple TV"})
        Cache TTL: 5 minutes
        API Call: None (data extracted from system info response)
        Used by: button.py (creates input switch buttons)
        
        Returns:
            dict: {input_id: label} e.g., {"1": "Sky Q", "2": "Apple TV"}
        """
        cache_key = "sources"
        cache_ttl = 300  # 5 minutes
        
        if self._is_cache_valid(cache_key, cache_ttl):
            return self._static_cache[cache_key]
        
        sources = {}
        io_data = data.get("io_data", {})
        input_video = io_data.get("input_video", [])

        for group in input_video:
            for label in group.get("labels", []):
                input_id = label.get("id")
                input_label = label.get("label")
                if input_id is not None and input_label:
                    # Convert to string for consistent key matching
                    sources[str(input_id)] = input_label

        _LOGGER.debug("Found %d input sources (cached)", len(sources))
        
        self._static_cache[cache_key] = sources
        self._cache_timestamp[cache_key] = asyncio.get_event_loop().time()
        
        return sources

    async def _get_cached_zones(self):
        """
        Get zone configuration with caching.
        
        Purpose: Get zone names, output assignments, settings
        Cache TTL: 5 minutes
        API Call: GET /api/data/102/ (only if cache expired)
        Used by: All platforms (button.py, number.py, sensor.py, switch.py)
        Reference: api.py → get_zones()
        
        Returns:
            list: Zone configuration objects
        """
        cache_key = "zones"
        cache_ttl = 300  # 5 minutes
        
        if self._is_cache_valid(cache_key, cache_ttl):
            return self._static_cache[cache_key]
        
        self._api_call_count += 1
        zones_meta = await self.api.get_zones()  # api.py → get_zones()
        zones = zones_meta.get("data", [])
        
        _LOGGER.debug("Found %d zones (cached)", len(zones))
        
        self._static_cache[cache_key] = zones
        self._cache_timestamp[cache_key] = asyncio.get_event_loop().time()
        
        return zones

    async def _get_cached_device_info(self, data):
        """
        Get MHUB device information with permanent caching.
        
        Purpose: Hardware/firmware details for HA device registry
        Cache TTL: Permanent (hardware doesn't change!)
        API Call: None (data extracted from system info response)
        Used by: All platforms for device_info dict
        
        Returns:
            dict: Device information (model, firmware, serial, etc.)
        """
        cache_key = "device_info"
        
        if cache_key in self._static_cache:
            return self._static_cache[cache_key]
        
        # New firmware uses "os" key, old firmware uses "mhub" key
        mhub_info = data.get("os") or data.get("mhub", {})
        
        device_info = {
            "api_version": mhub_info.get("api"),
            "model": mhub_info.get("product_code") or mhub_info.get("mhub_official_name", "MHUB"),
            "name": mhub_info.get("mhub_name", "MHUB"),
            "serial_number": mhub_info.get("serial_number"),
            "firmware": mhub_info.get("firmware") or mhub_info.get("mhub_firmware"),
            "os_firmware": mhub_info.get("os_firmware") or mhub_info.get("mhub-os_firmware"),
            "os_version": mhub_info.get("os_version") or mhub_info.get("mhub-os_version"),
            "unit_id": mhub_info.get("unit_id"),
            "ip_address": mhub_info.get("ip_address"),
        }
        
        self._static_cache[cache_key] = device_info
        
        _LOGGER.info(
            "MHUB System: %s | S/N: %s | API: %s | FW: %s",
            device_info["model"],
            device_info["serial_number"],
            device_info["api_version"],
            device_info["firmware"]
        )
        
        return device_info

    async def _get_ir_devices(self, data, stacked):
        """
        Build IR device map with aggressive caching.
        
        Purpose: Load IR pack configurations (button definitions from uControl)
        Cache TTL: Permanent (IR packs don't change - loaded once on startup)
        API Calls:
            - GET /api/data/201/ (IR pack summary)
            - GET /api/data/201/{port_id}/ (details for each port with IR pack)
        Used by:
            - button.py (creates IR button entities)
            - __init__.py (Pronto IR port mapping)
        Reference: api.py → get_ir_packs(), get_ir_pack_details()
        
        This is the most network-intensive part!
        Without caching: 6-10 API calls every poll
        With caching: 6-10 API calls ONCE on startup, then 0
        
        Returns:
            tuple: (ir_devices, ir_port_to_output)
                ir_devices: Dict of IR pack configurations
                ir_port_to_output: Dict mapping IR port IDs to output letters
        """
        cache_key = "ir_devices_full"
        
        # IR packs are static - only load once unless cache is cleared
        if cache_key in self._static_cache:
            cached = self._static_cache[cache_key]
            return cached["ir_devices"], cached["ir_port_to_output"]
        
        ir_devices = {}
        ir_port_to_output = {}  # Maps IR port number to output ID (for Pronto IR)
        
        io_data = data.get("io_data", {})
        ir_info = io_data.get("ir")
        
        if ir_info:
            backwards = ir_info.get("backwards")
            forwards = ir_info.get("forwards")

            backwards_start = (
                int(backwards.get("start_id"))
                if backwards and backwards.get("start_id")
                else None
            )

            forwards_start = (
                int(forwards.get("start_id"))
                if forwards and forwards.get("start_id")
                else None
            )
            
            forwards_ports = (
                int(forwards.get("ports", 0))
                if forwards and forwards.get("ports")
                else 0
            )

            # ========================================
            # GET IR PACK SUMMARY
            # ========================================
            # API Call: GET /api/data/201/ or /api/data/205/ (stacked)
            # Purpose: Find which ports have IR packs configured
            # Reference: api.py → get_ir_packs()
            self._api_call_count += 1
            packs = await self.api.get_ir_packs(stacked)
            pack_groups = (
                packs.get("data", [])
                if stacked
                else [packs.get("data")]
            )

            for group in pack_groups:
                if not group:
                    continue
                
                # ========================================
                # MAP AVR PORT (OUTPUT A - HDMI)
                # ========================================
                # Special case: Output A is HDMI (not HDBaseT)
                # Uses AVR IR port which is always: last_forward_port + 1
                # Reference: HDA API Documentation page 12
                has_avr = group.get("avr", False)
                if has_avr and forwards_start is not None and forwards_ports is not None:
                    avr_port = forwards_start + forwards_ports
                    ir_port_to_output[avr_port] = 'a'
                    _LOGGER.debug(
                        "Mapped AVR IR port %d to output A (HDMI)",
                        avr_port
                    )

                # ========================================
                # PROCESS INPUT, OUTPUT & GLOBAL PORTS
                # ========================================
                # "global" ports present on newer firmware (e.g., MHUBU41140)
                # They are standalone IR emitter ports not tied to an input/output
                for port_group in ["input", "output", "global"]:
                    ports = group.get(port_group, [])

                    # avr on older firmware is a boolean not a list - skip it
                    if not isinstance(ports, list):
                        continue

                    for index, port in enumerate(ports):
                        if not port:
                            continue

                        has_irpack = port.get("irpack", False)

                        # ========================================
                        # CALCULATE IR PORT NUMBER
                        # ========================================
                        if port_group == "input" and backwards_start is not None:
                            pid = backwards_start + index
                        elif port_group == "output" and forwards_start is not None:
                            pid = forwards_start + index
                            output_id = port.get("id", "").lower()
                            if output_id:
                                ir_port_to_output[pid] = output_id
                                _LOGGER.debug(
                                    "Mapped IR port %d to output %s (irpack=%s, index %d)",
                                    pid, output_id, has_irpack, index
                                )
                        elif port_group == "global":
                            # Global ports carry explicit id in the port object
                            try:
                                pid = int(port.get("id"))
                            except (ValueError, TypeError):
                                continue
                            _LOGGER.debug(
                                "Found global IR port %d (irpack=%s)", pid, has_irpack
                            )
                        else:
                            continue
                        
                        # ========================================
                        # LOAD IR PACK DETAILS
                        # ========================================
                        # Only load IR pack details if configured
                        if not has_irpack:
                            _LOGGER.debug(
                                "Skipping IR pack details for %s port %d (no IR pack configured)",
                                port_group, pid
                            )
                            continue

                        # API Call: GET /api/data/201/{port_id}/ or /api/data/205/{port_id}/ (stacked)
                        # Purpose: Get button definitions (command IDs, labels)
                        # Reference: api.py → get_ir_pack_details()
                        # This is EXPENSIVE - called for each port with IR pack!
                        # That's why we cache it permanently
                        try:
                            self._api_call_count += 1
                            details = await self.api.get_ir_pack_details(pid, stacked)
                            
                            if not details:
                                _LOGGER.warning(
                                    "No details returned for IR pack on %s port %d",
                                    port_group, pid
                                )
                                continue
                            
                            if "error" in details:
                                _LOGGER.warning(
                                    "Error loading IR pack on %s port %d: %s",
                                    port_group, pid, details.get("error")
                                )
                                continue

                            pack_data = details.get("data")
                            if not pack_data:
                                _LOGGER.warning(
                                    "Empty data for IR pack on %s port %d",
                                    port_group, pid
                                )
                                continue

                            # Debug: Log what fields we got
                            has_ir_pack = "ir_pack" in pack_data
                            has_irpack = "irpack" in pack_data
                            _LOGGER.debug(
                                "IR pack on %s port %d - has 'ir_pack': %s, has 'irpack': %s",
                                port_group, pid, has_ir_pack, has_irpack
                            )

                            # Store metadata about this IR pack
                            pack_data["_port_type"] = port_group
                            pack_data["_port_id"] = pid

                            ir_devices[f"{port_group}_{pid}"] = pack_data
                            
                            _LOGGER.debug(
                                "Loaded IR pack '%s' on %s port %d",
                                pack_data.get("name", "Unknown"),
                                port_group,
                                pid
                            )
                            
                        except Exception as e:
                            _LOGGER.error(
                                "Exception loading IR pack on %s port %d: %s",
                                port_group, pid, str(e)
                            )
                            continue

        _LOGGER.debug("Loaded %d IR device packs", len(ir_devices))

        # ========================================
        # CACHE PERMANENTLY
        # ========================================
        # IR packs don't change unless user edits them in uControl
        # Safe to cache forever (until HA restart or manual cache clear)
        self._static_cache[cache_key] = {
            "ir_devices": ir_devices,
            "ir_port_to_output": ir_port_to_output,
        }
        
        _LOGGER.info(
            "Loaded %d IR devices (CACHED - will not reload until restart)",
            len(ir_devices)
        )
        
        return ir_devices, ir_port_to_output

    # ========================================
    # CACHE UTILITY METHODS
    # ========================================

    def _is_cache_valid(self, key, ttl):
        """
        Check if cached data is still valid.
        
        Args:
            key: Cache key to check
            ttl: Time-to-live in seconds
            
        Returns:
            bool: True if cache exists and hasn't expired
        """
        if key not in self._static_cache:
            return False
        
        if key not in self._cache_timestamp:
            return False
        
        age = asyncio.get_event_loop().time() - self._cache_timestamp[key]
        return age < ttl
    
    def clear_cache(self):
        """
        Clear all cached data.
        
        Purpose: Force reload of all static data
        When to use:
            - After changing IR packs in uControl
            - After renaming zones/inputs
            - Troubleshooting stale data
            
        How to call:
            service: homeassistant.reload_config_entry
            data:
                entry_id: YOUR_MHUB_ENTRY_ID
        """
        _LOGGER.info("Clearing coordinator cache")
        self._static_cache.clear()
        self._cache_timestamp.clear()