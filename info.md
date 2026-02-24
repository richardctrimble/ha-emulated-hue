## Emulated Hue +

A custom Home Assistant integration that emulates a Philips Hue bridge for Amazon Alexa — fully configured through the UI.

> **Warning**
> You must disable or remove the built-in `emulated_hue` integration before installing this one.

### Features

- **UI-based configuration** — no YAML required.
- **Stable device IDs** — deleted IDs are permanently retired, preventing orphaned Alexa devices, also correctly responds so that removed device are removed from Alexa.
- **Full device management** — add, edit, delete, and list virtual Hue devices from the options flow.
- **Flexible entity linking** — link, re-link, or unlink Home Assistant entities at any time.

### Supported entity domains

`light` · `switch` · `fan` · `cover` · `climate` · `media_player` · `script` · `scene` · `input_boolean`

### Setup

1. Go to **Settings > Devices & Services > Add Integration**.
2. Search for **Emulated Hue +**.
3. Set the listen port (default `80` for Alexa compatibility).
4. Open **Configure** to manage devices and bridge settings.

### Device management

Access **Configure > Manage Devices** to:

- **Add** a virtual Hue device and link it to a Home Assistant entity.
- **Edit** the Alexa name or change the linked entity.
- **Delete** a device (its Hue ID is retired and never reused).
- **View** all devices with linked entities and statistics.
