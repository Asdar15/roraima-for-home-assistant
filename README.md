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
  those devices, with their states and their simple attributes, and can act only on them. Everything
  else stays private, even if a request for it comes from Roraima.
- **Roraima uses the actions Home Assistant offers for each entity.** The actions of a shared entity are
  the services of its own domain that Home Assistant applies to it, as in the automation editor: a lock
  is unlocked with `lock.unlock`, a cover is opened with `cover.open_cover`, a new kind of device brings
  its own services. A command carries only the fields Home Assistant describes for that service, each
  checked: a number within its limits, yes or no, one of the listed choices, or a short text. A field
  that points at another entity, device, area or target is never accepted, and each command reaches
  only the shared entity it names. Roraima cannot call any other service or restart Home Assistant.
  Parameters outside their limits are refused, not adjusted.
- **Scenes, scripts, automations and groups are never shared.** They run actions on other entities, so
  they could control devices you have not chosen; the same holds for any entity that lists other
  entities. An entity made from a template runs its own actions and can control other devices, so
  share one only if Roraima may use everything it controls.
- **An action counts as done when Home Assistant completes it.** After the service call the integration
  tells Roraima the state the entity reached. Roraima also receives the changes of the states and of
  the announced attributes of the shared entities, grouped at most once a second, and of no other
  entity.
- **Service entries are not offered.** Entries such as Backup, Sun or weather services are not devices
  you can rent out, so they never appear in the choice. Service entities of a shared device, such as
  a restart or factory reset button, are sent only as readings: Roraima can never press them.
- **Attributes stay simple.** Only numbers, texts and yes/no values travel, up to 16 per entity. The
  attributes that describe the entity (name, icon, picture, unit, class, supported features), lists,
  values that hold a web address and attributes whose name suggests a secret, such as a token, are
  never sent.

Names of actions, fields and attributes are sent in the language of your Home Assistant.

## Requirements

- Home Assistant 2026.1 or later.
- A Roraima seller account. Roraima operates in the 27 member states of the European Union.

## Installation

1. In Home Assistant, open the Marketplace (HACS before Home Assistant 2026.11) and add this repository
   as a custom repository of type *Integration*.
2. Install **Roraima for Home Assistant** and restart Home Assistant.

## Setup

1. In Home Assistant, go to **Settings → Devices & services → Add integration** and search for
   **Roraima**.
2. The window links to your Roraima panel, which opens on **Technologies → Home Assistant**. Press
   **Create pairing code**: the panel shows the code once.
3. Paste the code in the Home Assistant window, then choose the devices to share. You can change the
   selection at any time from the integration options.

## Your Roraima products in Home Assistant

Roraima tells Home Assistant how your time-based and token-based products are doing, for the
products that use at least one device of this connection. Each product appears as a device with
three sensors:

- **Status**: *Free* or *In use*.
- **Free at**: when the current use is expected to end; empty while the product is free.
- **Last sale**: when the product was last sold.

The sensors are unavailable while the connection to Roraima is closed. When the connection opens
again, Roraima sends the current list of products: a product that is no longer part of the
connection is removed from Home Assistant.

At every paid purchase, start and end of a use, Roraima sends a notice, which becomes the Home
Assistant event `roraima_event`. In the automation editor you can pick it as a device trigger of the
product (*Paid purchase*, *Use started*, *Use ended*), or listen to the event in YAML:

```yaml
automation:
  - alias: "Laundry light on when the washing machine is rented"
    triggers:
      - trigger: event
        event_type: roraima_event
        event_data:
          type: start
          product_id: 381
    actions:
      - action: light.turn_on
        target:
          entity_id: light.laundry
```

The event data holds `device_id`, `type` (`purchase`, `start` or `end`), `product_id`,
`product_name`, `kind` (`time` or `tokens`), `at`, `duration_minutes`, `tokens`, `program` and
`ends_at` (times in UTC). It never holds data about the buyer: no name, email, phone number, order
or access code.

**Do not use these notices to open doors or locks.** A notice is lost while the connection is
closed, and it does not prove who is at the door. Doors and locks that Roraima must open belong in
the access scenarios of the product, which Roraima runs on the shared devices.

These notices only go from Roraima to Home Assistant: the integration never sends commands, sales or
changes to Roraima.

## Removing the integration

Removing the integration from Home Assistant tells Roraima to revoke the pairing code. You can also
revoke the connection from the Roraima panel; Home Assistant then shows a notice under
**Settings → Repairs**.

## Support

Report problems in the [issue tracker](https://github.com/Asdar15/roraima-for-home-assistant/issues).
Please never include your pairing code in an issue.

## License

Apache License 2.0, see [LICENSE](LICENSE).
