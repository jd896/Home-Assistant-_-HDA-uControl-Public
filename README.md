# HDA uControl — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Home Assistant integration for [HDANYWHERE](https://hdanywhere.com) MHUB matrix systems using the HDA API v2.1.

## Supported Hardware

All MHUB systems supporting HDA API v2.1, including:

- MHUB U (4x3+1) / (8x6+2)
- MHUB U (4x1+1)
- MHUB PRO 2.0
- MHUB S
- MHUB (4x4) 100A

## Features

- Input switching buttons per zone
- Volume controls per zone
- Mute switches per zone
- Active source sensors per zone
- IR command buttons (requires uControl packs assigned in MHUB)
- CEC command buttons (per zone, configurable)
- Custom IR devices via Pronto hex codes (`custom_ir_devices.yaml`)
- Per-zone control method selection (IR / CEC / None)
- Per-zone command filtering — expose only the buttons you need
- Multi-instance support — multiple MHUB units simultaneously

## Installation

### HACS (recommended)

1. In HACS, go to **Integrations → Custom repositories**
2. Add `https://github.com/jd896/Home-Assistant-_-HDA-uControl` as an **Integration**
3. Search for **HDA uControl** and install
4. Restart Home Assistant

### Manual

1. Copy the `mhub_ucont` folder to `/config/custom_components/`
2. Restart Home Assistant

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **HDA uControl**
3. Enter the IP address of your MHUB
4. After the device is added, click ⚙️ **Configure** to set control methods per zone

## Configuration

### Zone Control Methods

In the Configure dialog, each zone can be set to:

- **IR** — creates buttons from the uControl pack assigned to that output port in the MHUB app
- **CEC** — creates HDMI-CEC command buttons for that zone's display
- **None** — no control entities created for that zone

After selecting a method, you will be prompted to select which commands to expose. All commands are selected by default.

After saving, reload the integration to apply changes.

### Custom IR Devices

For devices not in the uControl pack library (e.g. a projector with captured Pronto codes), create or edit `custom_ir_devices.yaml` inside the integration folder:

```yaml
custom_ir_devices:
  - name: "Bedroom Projector"
    mhub: "MHUB U 431"      # optional — matches integration entry title
    target: "output_c"       # output_a/b/c/d or input_1/2/3/4
    buttons:
      - name: "Power"
        pronto_code: "0000 006D 0022 0000 ..."
      - name: "Volume Up"
        pronto_code: "0000 006D 0022 0000 ..."
```

The `mhub` field is optional. If omitted the device loads on all instances.

### Pronto IR Service

A service `mhub_ucont.send_pronto_ir` is available for sending raw Pronto IR codes from automations:

```yaml
service: mhub_ucont.send_pronto_ir
data:
  target: "output_c"
  pronto_code: "0000 006D 0022 0000 ..."
```

Or using a direct port number:

```yaml
service: mhub_ucont.send_pronto_ir
data:
  port: 7
  pronto_code: "0000 006D 0022 0000 ..."
```

## Notes

- API polling interval is 5 seconds
- IR pack data is cached permanently until integration reload
- Zone config is cached for 5 minutes
- Volume and mute use optimistic updates for instant UI response
