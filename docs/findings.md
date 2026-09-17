# Findings

Things that cost real time, in the order they bit. Every one of these is
measured on hardware, not reasoned from documentation.

## Car stereos ignore filenames

Tracks were numbered `001 -`, `002 -` and played in a scrambled order anyway.
Head units play files in the order the **FAT directory entries were written**,
and rsync writes in source-walk order, which on ext4 is hash order:

```
/Workout as written:  002, 001, 007, 003, 005, 004, 006, 008, 011, 009, 010, 013, 012
```

`fatsort` rewrites the entries in place without touching file data, so it costs
nothing on an incremental sync. Two passes are needed:

```sh
fatsort -n -D / "${LOOP}p1"    # everything, natural name order
fatsort -t -d / "${LOOP}p1"    # root ONLY, by mtime
```

The second pass is what keeps *folders* in a chosen order rather than
alphabetical — stamp the directories on the source and the root sort follows.
`fatsort -l -d <dir> <device>` prints the physical order without changing it,
which is the only way to actually check this. The filesystem must be unmounted.

## FAT32 and non-ASCII filenames

A default `mount -t vfat` comes up `iocharset=ascii`. Every Cyrillic filename
failed with `Invalid argument (22)` — silently eating a large part of the
library, since rsync reports it per-file and carries on.

```sh
mount -o uid=1000,gid=1000,utf8=1,codepage=866 ${LOOP}p1 /mnt/sync
```

Long filenames are stored as UTF-16 in FAT32 regardless, so the head unit
renders them fine once they are written correctly.

## resize2fs refuses to shrink a clean filesystem

Shrinking root from initramfs failed repeatedly with `Please run 'e2fsck -f'
first` — *after* `e2fsck` had returned 0 and reported the filesystem clean.

Two separate causes stacked:

1. `e2fsck` returns **1** on the first pass when it cleans up orphan inodes, and
   resize2fs will not touch a filesystem that was just repaired. Loop until 0.
2. resize2fs compares `s_lastcheck` against `s_mtime`, and **a Pi has no RTC**.
   In initramfs the clock is behind the last mount time recorded during a
   properly-clocked boot, so the check fails on a healthy filesystem.

`resize2fs -f` skips exactly that comparison (`!force && ...` in its source).
Setting the clock with `date -s` first is worth doing anyway.

Related, in the same area: `sfdisk --append` silently failed to persist, and
`partx` is not installed on RPi OS Lite. `parted -s <disk> unit s mkpart primary
ext4 <start>s <end>s` both wrote the table and got the running kernel to see the
new partition with root still mounted.

## Debugging things that run before userspace

Initramfs output goes to the console, which is invisible over SSH. Write to
`/dev/kmsg` instead and it lands in `dmesg`, surviving into the booted system.

**Collapse newlines first.** A multi-line tool message becomes separate kmsg
records, and grepping for your own prefix then hides every line but the first.
That is how the real resize2fs error stayed invisible through two attempts:

```sh
log() { echo "carmp3: $(echo "$*" | tr '\n' ' ')" > /dev/kmsg 2>&1; }
```

## `/sys/class/udc/*/state` lies

In peripheral mode `dwc2` leaves it reading `not attached` even while a host is
actively enumerating the device. Chasing that cost an hour on a gadget that was
working perfectly. `dmesg` is the truth:

```
dwc2 fe980000.usb: bound driver configfs-gadget.carmp3
dwc2 fe980000.usb: new device is high-speed
dwc2 fe980000.usb: new address 16        <- the host enumerated us
```

## One unclean power cut deleted the WiFi profile

`/etc/NetworkManager/system-connections/` was empty, with an mtime matching the
minute power was cut. The Pi came back with a working radio, no saved network
and no route to the NAS — which looks exactly like a hardware fault and is not.
Nothing in `lost+found`; the file was simply gone.

A car does that on **every trip**. This is the whole argument for read-only root,
demonstrated rather than asserted.

Recovery, if it happens before overlayfs is enabled: Raspberry Pi Imager leaves
the credentials in `/boot/firmware/network-config` on the boot partition. The
SSID is the quoted key under `access-points:`, and the stored password is
already a 64-character PSK, which `nmcli` takes directly.

## systemd ordering against a `nofail` mount

The gadget service failed every boot, and running the same script by hand
afterwards worked — the signature of an ordering bug, not a broken script.

```
[7.403] Mounting data.mount - /data...
[7.583] Starting carmp3-gadget.service...
[7.660] no active image: /data/images/A.img
[7.800] Mounted data.mount - /data.        <- 140 ms too late
```

`/data` is mounted `nofail`, so its mount is **not** required by
`local-fs.target`, and ordering only against that target races. The fix is
`RequiresMountsFor=/data`.

## A partially-conducting transistor

The buzzer was silent. With the GPIO held high, collector-to-emitter measured
**1.2 V** — neither saturated (0.1–0.3 V) nor off (~5 V).

That middle reading is the signature of **reverse-active mode**: a BJT still
conducts with collector and emitter swapped, but its gain collapses from ~100 to
about 2–5, so it never saturates. Swapping the two outer legs fixed it.

Do not trust the part marking for orientation. A TO-92 stamped plain `2N2222` is
made by many houses and the pinout is not consistent; `PN2222A` and `2N3904` are
E-B-C with the flat toward you, while `BC337` and `BC547` are C-B-E.

## Endpoint protection cuts USB port power

Testing on a corporate laptop, SentinelOne blocked the device — and it does not
merely refuse to mount the volume, it **disables the port**, which cuts VBUS. On
a Pi 4 the USB-C is the only power input, so the Pi silently switched off and
dropped off the network mid-session. Test on a personal machine.

Amusingly, the block message names the device from the gadget's own descriptor
strings, which is a decent confirmation they are set correctly.

## `vcgencmd display_power 0` does nothing under KMS

It is a legacy-graphics-stack command and is ignored when `dtoverlay=vc4-kms-v3d`
is active. Blanking HDMI needs `video=HDMI-A-1:d` on the kernel command line —
but consider leaving HDMI alone. With no monitor attached the saving is
negligible, and the console is the recovery path when the unit drops off WiFi.

## overlayroot overlays everything in fstab, not just root

Enabling the overlay filesystem makes root copy-on-write to RAM, which is the
whole point. But `overlayroot` scans `/etc/fstab` and does the same to **every**
filesystem listed there. A separate data partition in fstab becomes:

```
/dev/mmcblk0p3 on /media/root-ro/data   ext4  (ro,noatime)   <- real, read-only
/media/root-ro/data on /data            overlay (rw, upperdir=tmpfs)
```

Everything written to it now lives in RAM and is discarded at reboot. In this
build a sync started straight away and began pushing 2.8 GB into a 1.9 GB tmpfs;
shared memory reached 1.5 GiB before it was stopped.

Keep the data partition **out of fstab** and mount it with a systemd unit
instead, which overlayroot does not touch.

## A mount unit must not be ordered after the target that wants it

```ini
[Unit]
After=local-fs.target        # <- wrong, with WantedBy=local-fs.target below
[Install]
WantedBy=local-fs.target
```

That is a cycle, and systemd breaks it by dropping the mount:

```
data.mount: Found ordering cycle on local-fs.target/start
data.mount: Job data.mount/start deleted to break ordering cycle
```

Mount units already get `After=local-fs-pre.target` and `Before=local-fs.target`
from default dependencies, so the `After=` line is not merely redundant. The
symptom is a unit that never mounts at boot but mounts perfectly when started by
hand.

## Log timestamps early in boot are wrong

A Pi has no RTC. It restores an approximate clock at boot, anything running in
the first seconds logs against it, and NTP corrects a few seconds later. Log
lines can therefore be stamped minutes *earlier* than they happened and appear
to predate entries already in the file. Compare `uptime -s` with `date` before
concluding a log is stale — it cost a round of confusion here.
