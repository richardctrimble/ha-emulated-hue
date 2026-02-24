# Emulated Hue +

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

A custom [Home Assistant](https://www.home-assistant.io/) integration that emulates a Philips Hue bridge, allowing Amazon Alexa to discover and control Home Assistant entities via voice commands — all configured through the UI.

> **Warning**
> You must disable or remove the built-in `emulated_hue` integration **before** installing this one. The setup will fail if it detects a conflict.

## Features

- **UI-based configuration** — set up and manage everything from Settings > Integrations. No YAML required.
- **Stable device IDs** — deleted Hue IDs are permanently retired and never reused, preventing orphaned devices in the Alexa app.
- **Full device management** — add, edit, delete, and list virtual Hue devices through the options flow.
- **Flexible entity linking** — link a Hue device to any supported entity, change the link later, or leave it unlinked.

## Supported entity domains

`light`, `switch`, `fan`, `cover`, `climate`, `media_player`, `script`, `scene`, `input_boolean`

## Installation

### HACS (recommended)

1. Open **HACS > Integrations**.
2. Select the three-dot menu > **Custom repositories**.
3. Enter `https://github.com/richardctrimble/ha-emulated-hue` and choose category **Integration**.
4. Click **Add**, then find **Emulated Hue +** and install it.
5. **Restart Home Assistant.**

### Manual

Copy the `custom_components/ha_emulated_hue` folder into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

1. Go to **Settings > Devices & Services > Add Integration**.
2. Search for **Emulated Hue +**.
3. Enter the listen port (default `80`, required for Alexa compatibility).
4. Optionally set an advertise IP and port if your network requires it.

Only one instance of the integration is allowed.

### Configuration options

| Option | Default | Description |
|--------|---------|-------------|
| Listen port | `80` | Port the emulated Hue bridge listens on. Alexa requires port 80. |
| Advertise IP | Auto-detect | IP address advertised to Alexa during discovery. |
| Advertise port | Listen port | Port advertised to Alexa during discovery. |

These can be changed later under **Configure > Settings**.

## Device management

Open **Settings > Devices & Services > Emulated Hue + > Configure** to access the device management menu:

| Action | Description |
|--------|-------------|
| **Add Device** | Create a virtual Hue device, give it an Alexa-friendly name, and optionally link it to a Home Assistant entity. |
| **View Devices** | See all devices with their Hue IDs, linked entities, and statistics. |
| **Edit Device** | Change the name or re-link to a different entity. |
| **Delete Device** | Permanently remove a device. Its Hue ID is retired and never reused. |

## Services

The integration registers the following services for development and testing:

| Service | Description |
|---------|-------------|
| `ha_emulated_hue.reload` | Reload device data from storage. |
| `ha_emulated_hue.test_create_device` | Create a device via service call (accepts `name` and `entity_id`). |
| `ha_emulated_hue.test_list_devices` | Log all current devices to the Home Assistant log. |

## Troubleshooting

- **"The built-in emulated_hue integration is active"** — Remove the built-in integration from Settings > Devices & Services before adding this one.
- **Port 80 already in use** — Another service is using port 80. Check for conflicts with reverse proxies or other add-ons.
- **Alexa cannot discover devices** — Ensure the listen port is `80` and that your Home Assistant host is reachable on the local network.

## Licence

Released under the [MIT Licence](https://github.com/richardctrimble/ha-emulated-hue/blob/master/LICENSE).

### Attribution

Built upon the following prior work, each released under the MIT Licence:

| Project | Author | Licence |
|---------|--------|---------|
| [emulated-hue-advanced](https://github.com/alexlenk/emulated-hue-advanced) | [@alexlenk](https://github.com/alexlenk) | [MIT](https://github.com/alexlenk/emulated-hue-advanced/blob/master/LICENSE) |
| [Emulated Hue](https://www.home-assistant.io/integrations/emulated_hue/) (Home Assistant Core) | [@home-assistant](https://github.com/home-assistant) | [MIT](https://github.com/home-assistant/core/blob/dev/LICENSE.md) |
| [ha-local-echo](https://github.com/blocke/ha-local-echo) | [@blocke](https://github.com/blocke) (Bruce Locke) | [MIT](https://github.com/blocke/ha-local-echo/blob/master/LICENSE) |
