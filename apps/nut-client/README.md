# WARNING

!WARNING! This is a work-in-progress and not yet functional !WARNING!

# NUT-Client for Rinkhals

This app monitors a UPS device connected to the printer to allow for automatic pausing of a print job during a power outage.

## Installation

The app will be installed automatically when added through the Rinkhals app system.

## Configuration

Before the NUT client can be used, you need to configure your NUT server address and optional credentials.

The app includes a sample config file that you can edit:

1. Copy the sample file to create your token file:
   ```
   cp /useremain/home/rinkhals/apps/nut-client/config/nut-client.conf.sample /useremain/home/rinkhals/apps/nut-client/config/nut-client.conf
   ```

2. Edit the token file:
   ```
   nano /useremain/home/rinkhals/apps/nut-client/config/nut-client.conf
   ```

3. Configure the included options to match your NUT server.

### Where do I get a UPS, and what kind?

TODO: Talk about picking out a UPS with enough capacity and USB or serial that is compatible with NUT.

### Where do I get a NUT server?

TODO: Talk about having a Raspberry Pi, mini-PC, or other device or system that can be installed with a NUT server connected to the same UPS.
TODO: Talk about configuring said device as a NUT server.
TODO: Talk about possible future ability for the printer to act in standalone mode without an external NUT server should a driver be built.

### Example nut-client.conf File

TODO: Enter example nut-client.conf file

## Usage

### Starting the NUT client

The NUT client will start automatically when the app is started. You can manually start it with:

```
/useremain/home/rinkhals/apps/nut-client/app.sh start
```

### Checking Status

To check if the NUT client is running:

```
/useremain/home/rinkhals/apps/nut-client/app.sh status
```

### Stopping the NUT client

To stop the NUT client:

```
/useremain/home/rinkhals/apps/nut-client/app.sh stop
```

## What happens when there is a power outage?

TODO: Talk about how it checks UPS state, battery charge level, and what actions it takes at different states and charge levels.

## Security Considerations

TODO: Finish filling this out
- Talk about: NUT protocol is unencrypted
- Talk about: NUT server must be configured correctly to prevent unauthorized evices from making config changes in the UPS.
- Talk about: Do not expose your NUT server to the internet. Talk about firewalling.

## Additional Resources

- [NUT server documentation](https://...)
- [Something about the NUT protocol]()
- [Something about NUT clients]()