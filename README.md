# Roraima for Home Assistant

Unofficial integration. Home Assistant is a trademark of the Open Home Foundation.

This integration connects your Home Assistant to your [Roraima](https://roraima.io) account, so that
the devices you choose can be used in the rentals and services you sell on Roraima.

## How it works

- **Your home opens the connection.** Home Assistant connects out to Roraima. You do not need a public
  address, port forwarding or a remote access subscription, and Roraima never receives a Home Assistant
  access token.
- **You decide what Roraima can see and control.** At first no device is shared. You pick the devices
  in the integration options; Roraima receives only the name, manufacturer, model and the entities of
  those devices, and can act only on them. Everything else stays private, even if a request for it
  comes from Roraima.
- **Each action uses the service of its own domain.** A lock is unlocked with `lock.unlock`, a cover is
  opened with `cover.open_cover`. Scenes, scripts and automations are never shared, and Roraima cannot
  call arbitrary services or restart Home Assistant.

Shared domains: `switch`, `light`, `lock`, `cover`, `fan`, `climate`, `valve`, `button`,
`input_boolean`, and `sensor` and `binary_sensor` as read-only.

## Requirements

- Home Assistant 2026.1 or later.
- A Roraima seller account. Roraima operates in the 27 member states of the European Union.

## Installation

1. In Home Assistant, open the Marketplace (HACS before Home Assistant 2026.11) and add this repository
   as a custom repository of type *Integration*.
2. Install **Roraima for Home Assistant** and restart Home Assistant.

## Setup

1. In your Roraima panel, open **Devices** and choose **Connect Home Assistant**. The panel shows a
   pairing code once.
2. In Home Assistant, go to **Settings → Devices & services → Add integration**, search for
   **Roraima** and enter the code.
3. Choose the devices to share. You can change the selection at any time from the integration
   options.

## Removing the integration

Removing the integration from Home Assistant tells Roraima to revoke the pairing code. You can also
revoke the connection from the Roraima panel; Home Assistant then shows a notice under
**Settings → Repairs**.

## Support

Report problems in the [issue tracker](https://github.com/Asdar15/roraima-for-home-assistant/issues).
Please never include your pairing code in an issue.

## License

Apache License 2.0, see [LICENSE](LICENSE).
