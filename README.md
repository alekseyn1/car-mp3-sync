# car-mp3-sync

A Raspberry Pi that lives in your car, powered from the head unit's own USB
port, and pretends to be a USB stick full of MP3s. When you start the engine at
home it joins WiFi, pulls the latest library from a NAS, and hands the stereo a
freshly updated drive — with the tracks in the right order.

No phone, no Bluetooth pairing, no swapping USB sticks.

```
NAS  --(SMB, read-only)-->  Pi Zero 2 W / Pi 4  --(USB mass storage)-->  head unit
        over home WiFi            in the glovebox              one cable, power + data
```

## Why it works the way it does

**One USB port carries both power and data.** This is not a trick — it is what
USB peripheral mode is for. The head unit is the USB *host*: it puts 5 V on VBUS
and enumerates whatever answers on D+/D−. The Pi runs off that 5 V and answers as
a mass-storage device on the same four wires.

**Sync happens on departure, not arrival.** Most car USB ports die the instant
the ignition is switched off, so there is no power to sync with when you get
home. Instead the Pi syncs while you start the car and pull out of the driveway,
still in range of your WiFi. That turns out to be the *better* side of the trade
if your downloader runs overnight: a morning sync picks up last night's tracks,
where an evening one would miss them for another whole day.

**Two images, flipped.** You cannot write to a FAT32 volume while the head unit
has it mounted. So there are two: the car plays from one while the sync writes
into the other, and the pointer flips only after a clean unmount and an fsck.

```
boot      -> bind image A -> car plays from A
                          -> rsync writes into B -> fsck -> flip
next boot -> bind image B -> car plays from B
                          -> rsync writes into A -> fsck -> flip
```

A failure anywhere before the flip leaves the pointer alone, so the worst case
is that the car keeps playing yesterday's copy.

## Status

Built and running. Measured on a Pi 4 from cold power:

| | |
|---|---|
| Drive present to the host | **7.5 s** |
| Sync complete, pointer flipped | **21 s** |
| Library | 200 files, 2.8 GB |
| Idle temperature after tuning | 49 °C |

## Hardware

Any Pi whose USB port reaches the SoC's OTG controller:

| Board | Gadget mode |
|---|---|
| Zero / Zero W / **Zero 2 W** | yes — micro-USB port marked `USB`, **not** `PWR IN` |
| **Pi 4 / 400 / CM4** | yes — the USB-C port |
| Pi 3A+ | yes — no hub, USB-A wired direct |
| **Pi 3B / 3B+ / 2 / 1** | **no** — USB sits behind the LAN9514 hub |
| Pi 5 | **no** — USB-C is power only |

The **Zero 2 W is the right board** for a car: a third of the power draw and
rated −20 to +70 °C, against the Pi 4's 0–50 °C. The Pi 4 works and is fine for
bench work, but it is out of spec in a hot car and its boot surge is marginal on
a weak USB port.

Plus a microSD card (32 GB is ample), a **data** USB cable, and optionally an
active piezo buzzer with an NPN transistor, 1 kΩ and 10 kΩ — see
[docs/hardware.md](docs/hardware.md).

## What it sounds like

The unit has no screen and lives in a glovebox, so the beep is the whole UI:

| Pattern | Meaning |
|---|---|
| 1 short | Synced, files changed |
| 2 short | Already up to date |
| 3 short | WiFi not found — you are not at home |
| 1 long | Sync failed, check the log |

## Install

See [docs/build.md](docs/build.md) for the full walkthrough. In brief:

1. Flash Raspberry Pi OS Lite, enable SSH and WiFi.
2. Enable USB peripheral mode: `dtoverlay=dwc2,dr_mode=peripheral` in
   `config.txt`, `modules-load=dwc2` appended to `cmdline.txt`.
3. Carve out a writable `/data` partition — the installer's root fills the card,
   and `docs/build.md` covers shrinking it in place over SSH.
4. Create the A/B images, copy in `pi/bin/*` and `pi/systemd/*`, enable the units.
5. Create a **dedicated read-only NAS account** and put its credentials in
   `/etc/carmp3-nas.cred`.
6. Make root read-only, last.

## The findings

The parts that cost real time are written up in
[docs/findings.md](docs/findings.md). The short version:

- Car stereos play files in **FAT directory-entry order**, not filename order.
  You need `fatsort`, or your carefully numbered tracks play at random.
- FAT32 mounts default to `iocharset=ascii`, and any non-ASCII filename fails to
  copy with `EINVAL`. `utf8=1` is mandatory if your library is not all English.
- `resize2fs` refuses to shrink a *clean* filesystem from initramfs, because a
  Pi has no RTC and its last-check timestamp looks older than its last mount.
- `/sys/class/udc/*/state` reads `not attached` while a host is actively talking
  to the device. Trust `dmesg`, not that file.
- A single unclean power cut deleted the saved WiFi profile. In a car that
  happens every trip, which is why root is read-only.

## The NAS side

`nas/youtube.py` downloads YouTube playlists as MP3s with cover art, one folder
per playlist, numbered in playlist order. It is included because the sync
expects that layout, but any directory of MP3s will do.

## Licence

MIT.
