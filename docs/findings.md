# Findings

Things that cost real time, in the order they bit. Every one of these is
measured on hardware, not reasoned from documentation.

## Car stereos ignore filenames - and there are two reasons why

Tracks were numbered `001 -`, `002 -` and played scrambled anyway. There are two
independent mechanisms behind this, and conflating them cost a round trip.

### Mechanism 1: FAT directory-entry order

Many head units play files in the order the **FAT directory entries were
written**, and rsync writes in source-walk order, which on ext4 is hash order:

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
alphabetical - stamp the directories on the source and the root sort follows.
`fatsort -l -d <dir> <device>` prints the physical order without changing it,
which is the only way to actually check this. The filesystem must be unmounted.

### Mechanism 2: the ID3 TRCK frame

**This is what actually fixed the unit in this build.** Entry order was already
correct after `fatsort`, and playback was still scrambled. The files carried a
title tag but **no track number at all**, so the unit was sorting 200 files that
each claimed to have none.

```python
audiofile.tag.track_num = (position, total)
```

After re-tagging and re-syncing, tracks played in order. Shuffle was off.

### What to take from this

Do both. `fatsort` is free and plenty of units really do use entry order; the ID3
tag is what mattered here. And test one change at a time - both were applied
close together, and only the user confirming shuffle was off made it clear which
had done the work.

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

## Tailscale's node identity lives on root, which read-only root discards

Installing Tailscale on a unit with an overlay root looks fine and works
perfectly - until the first reboot, when the node has forgotten who it is and
wants an auth key that has probably expired by then.

`tailscaled` keeps its state in `/var/lib/tailscale`, which is on root, so under
`overlayroot=tmpfs` it is RAM-backed. Move it to the writable partition:

```ini
# /etc/systemd/system/tailscaled.service.d/state.conf
[Service]
ExecStart=
ExecStart=/usr/sbin/tailscaled --state=/data/tailscale/tailscaled.state \
  --statedir=/data/tailscale --socket=/run/tailscale/tailscaled.sock --port=${PORT}
```

**Clearing `ExecStart=` first is the part that matters.** The packaged unit
hardcodes `--state=/var/lib/tailscale/...`, so simply adding flags via
`/etc/default/tailscaled` produces a command line with `--state=` twice and
leaves you depending on which one the flag parser happens to honour. That is not
a contract worth relying on. Check what is actually running:

```sh
tr '\0' ' ' < /proc/$(pgrep -x tailscaled)/cmdline
```

## Do not judge the network from one ping at boot

The first version of the route picker tried the LAN once, and if that failed
went to the tailnet. A car sitting in its own driveway then synced over the VPN,
because the sync starts around 16 s into boot and WiFi has often not finished
associating yet.

Wait for a default route to exist, then give the LAN several attempts before
falling back. The symptom is subtle - everything works, just over the slow and
more expensive path.

## A shell-sourced config needs its values quoted

`NAS_ROUTE_ORDER=vpn lan` in a file that gets `. sourced` does not set a
two-word variable. It sets `NAS_ROUTE_ORDER=vpn` *for the duration of a command
called `lan`* — which does not exist. The variable ends up unset and the code
falls back to its default, while everything appears to work.

```
carmp3.conf: line 7: lan: command not found
```

That message was in the output and easy to read past. Quote anything containing
a space.

## overlayfs does not see edits to its lower layer

With read-only root, the way to make a persistent change is to write to the real
root under `/media/root-ro/`. That works — but **the running system will not
notice**. overlayfs explicitly does not support modifying the lowerdir while
mounted, so the overlay keeps serving a cached inode and the edit looks like it
silently failed:

```
overlay shows : NAS_ROUTE_ORDER=vpn lan       <- stale
disk has      : NAS_ROUTE_ORDER="vpn lan"     <- the edit did land
upper copy    : none
```

Verify by reading the file under `/media/root-ro/`, not at its normal path, and
reboot to make it live. Checking `md5sum` of both paths is a quick way to tell
whether you are looking at a cached copy.

## Embedded cover art has two separate failure modes

Art that will not display in a head unit is usually blamed on one thing. It was
two here, affecting overlapping sets of files, so fixing the first revealed
nothing and looked like a failure.

**Format and tag version.** Files produced by yt-dlp carried JPEG art in an
ID3v2.4 tag and never displayed; files from the older pipeline carried PNG in
ID3v2.3 and did. `--convert-thumbnails png` plus
`tag.save(version=eyed3.id3.ID3_V2_3)` brings new downloads into line.

**Byte size.** Independently of format, art above roughly 400 KB does not load.
The evidence was two files identical in every other respect:

| Track | Art | Dimensions | ID3 | Displays |
|---|---|---|---|---|
| 009 | PNG 1283 KB | 1280x720 | v2.3 | no |
| 022 | PNG **398 KB** | 1280x720 | v2.3 | yes |
| 010 | PNG 126 KB | 480x360 | v2.3 | yes |

Same dimensions on 009 and 022, so it is not resolution. Downscaling the 78
oversized files to 640 px and about 250 KB fixed every remaining one.

Diagnose by dumping the APIC frame length straight from the file rather than
trusting a tag editor, and compare a working file against a broken one field by
field before forming a theory. Two confident theories died here before the data
settled it.

## Sparse images grow until they fill the disk

The A/B images are created with `truncate -s 8G`, so they start near zero and
hold only what is written. But rsync replacing a file frees the old clusters
*inside* FAT32 while ext4 keeps those blocks allocated in the backing file. The
image therefore creeps toward its full apparent size no matter how little it
contains - 2.8 GB of music was occupying 7.5 GB after enough sync cycles.

When `/data` filled, the image went read-only mid-write and the sync failed. The
image was left dirty, so every retry failed identically, and the five-minute
retry timer turned that into a failure beep every five minutes.

Mount with `-o discard` and run `fstrim` before unmounting. It reclaimed 5.2 GiB
per image here, and the sync now does it every run.

## Repair the image before writing, not only after

The sync fsck'd the image after rsync, to avoid handing the car something that
would not mount. That guards the wrong end. A run that dies mid-write leaves the
image dirty, and vfat then remounts read-only the moment rsync touches it, so
every later sync fails the same way with no path to recovery.

`fsck.vfat -a` before mounting makes it self-healing. But note the side effect:
fsck salvages orphaned cluster chains into `FSCK####.REC` files at the image
root, and rsync then tries to delete them and trips `--max-delete`, failing the
sync for a different reason. Delete that debris explicitly so the delete guard
stays meaningful for actual content.

## A changing volume serial makes the car think it is a new stick

`mkfs.vfat` derives the volume serial from the clock, so the two ping-pong
images get different ones. The head unit then sees an unfamiliar volume on every
single boot, because the images alternate - re-running first-time setup, or
re-prompting to store voice-recognition data, each trip.

Pin both images to one serial. It lives at offset `0x43` of the FAT32 boot
sector, little-endian, mirrored in the backup boot sector at sector 6, and the
filesystem must be unmounted to change it. `pi/bin/carmp3-volid` does this and
the sync applies it on every run, so a rebuilt image does not revert.

## Keep the journal when root is read-only

`overlayroot` puts `/var/log/journal` in RAM, so each boot's log dies with it -
and a unit that reboots unexpectedly in a car erases its own evidence. Symlink
`/var/log/journal` to a directory on the writable partition and set
`Storage=persistent` with a size cap. `systemd-journal-flush` moves the early
boot records across once the partition is mounted, so nothing is lost.

Verify with `journalctl --list-boots` after two reboots: boot `-1` should still
be readable.

## overlayroot makes four boot services fail, harmlessly

Every boot prints what looks like a serious failure:

```
systemd-remount-fs[1595]: mount: /: fsconfig() failed:
                          overlay: No changes allowed in reconfigure.
Dependency failed for rpi-setup-loop@var-swap.service
Dependency failed for systemd-zram-setup@zram0.service
Dependency failed for dev-zram0.swap
Dependency failed for rpi-resize-swap-file.service
```

`systemd-remount-fs` re-mounts `/` according to `/etc/fstab`, and overlayfs
rejects a reconfigure outright. The other three are swap setup, which orders
itself after that unit and so never runs. Nothing here needs fixing: swap on a
read-only-root Pi is pointless, and root is already mounted the way it should
be. Mask the swap units if the console noise bothers you, but leave
`systemd-remount-fs` alone - other things order themselves after it.

Worth knowing so it is not mistaken for the cause of a real fault. It was, here,
during an unrelated hunt.

## The WiFi profile is not where you would expect, and cloud-init owns it

Raspberry Pi OS applies the Imager's WiFi settings through cloud-init, from a
seed at `/boot/firmware/network-config`. That becomes netplan YAML in
`/etc/netplan/90-NM-<uuid>.yaml`, which NetworkManager renders into a connection
under **`/run/NetworkManager/system-connections/`** - a tmpfs, rebuilt on every
boot.

```
netplan-wlan0-Skywalker  ->  /run/NetworkManager/system-connections/   volatile
ValMe                    ->  /etc/NetworkManager/system-connections/   persistent
```

So `nmcli con show` lists the network you rely on, and the file behind it does
not survive a reboot on its own. Before disabling cloud-init, write a real
profile into `/etc/NetworkManager/system-connections/` - on a read-only root
that means `/media/root-ro/...`, with the lower layer remounted rw. Copy the
runtime file rather than retyping the key, give it a fresh UUID, and `chmod
600`; NetworkManager ignores a group- or world-readable profile.

Add a wired profile at the same time. `eth0` comes from the same seed, so a unit
with no WiFi has no fallback either.

The netplan YAML itself *is* persistent, so NetworkManager's netplan backend
would probably rebuild the connection without cloud-init. An explicit profile
removes the question, which is worth more than the argument when the unit is
about to be 8,000 km away.

## Disabling cloud-init takes 12 seconds off the boot

Raspberry Pi OS runs cloud-init on every boot to apply the Imager's
customisation, long after there is anything left to apply. On a unit whose whole
design depends on syncing before the car leaves WiFi range, that is 12 seconds
of the window spent re-deciding settled questions.

Measured on `mp3drive-sfax`, same card, same charger, one reboot apart:

| | before | after |
|---|---|---|
| total | 46.690s | **35.048s** |
| userspace | 43.687s | 32.085s |
| NetworkManager starts | @14.740s | @6.587s |
| `network-online.target` | @30.091s | **@22.135s** |
| `multi-user.target` | @43.402s | @32.082s |

`network-online.target` is the number that matters - nothing can sync before it.
It moved 8 seconds earlier, because cloud-init sits in the `sysinit.target`
chain ahead of NetworkManager rather than costing time on its own; only
`cloud-init-main` was individually slow, at 7.8s.

Disable it the supported way, which on a read-only root means the lower layer:

```sh
mount -o remount,rw /media/root-ro
touch /media/root-ro/etc/cloud/cloud-init.disabled
mount -o remount,ro /media/root-ro
```

**Write a persistent WiFi profile first** - see the section above on where the
profile actually lives. Do not automate this in `install.sh`: on a machine whose
WiFi came from the Imager seed it can remove the only route back in.

For comparison, the USB gadget binds in under a second and is not worth tuning.
The car sees the drive almost immediately; it is the network that is slow.

## The case fan: prove the pin controls it before trusting an overlay

A case whose instructions say "5V, GND, TXD" is telling you the fan's control
line goes to GPIO14. That does not mean the Pi is driving it. On a stock image
with no serial console, GPIO14 is an undriven input:

```
14: ip    pn | hi   // input, no pull, reading high
```

The fan still runs, because its control input floats high on its own. So it
looks like it works and is in fact completely uncontrolled - full speed whenever
the board has 5 V. Two theories died here before the pin state was read: that
TXD was idling high from the UART (there is no UART on it - `enable_uart` is
unset) and that noise on the UART pins was spawning a getty storm
(`serial-getty@ttyS0` is disabled).

**Test the pin before adding the overlay.** Drive it and watch the SoC
temperature - objective, and it needs nobody watching the fan:

| phase | pin | temp | delta |
|---|---|---|---|
| HIGH | driven high | 58.9 -> 43.8C | **-15.1** |
| LOW | driven low | 42.3 -> 52.1C | **+9.8** |
| HIGH | driven high | 54.0 -> 40.9C | **-13.1** |

Reversible across two independent HIGH phases, so the pin really does gate the
fan, active-high - which is the polarity `gpio-fan` assumes.

`/boot/firmware` is a real vfat partition mounted **ro**, not part of the
overlay, so the edit persists once you remount it:

```sh
mount -o remount,rw /boot/firmware
printf '\ndtoverlay=gpio-fan,gpiopin=14,temp=60000\n' >> /boot/firmware/config.txt
mount -o remount,ro /boot/firmware
```

After reboot the kernel owns the pin and the fan is off below the trip:

```
cooling_device0: type=gpio-fan cur=0 max=1
trip_point_0_temp: 60000 (active)
14: op -- pn | lo        // was "ip pn | hi"
```

Verified under load: at 54C `fan=0`, and one sample later at 64.2C `fan=1`.

### Do not stress-test a USB-powered Pi with synthetic load

Pushing four cores to 100% to force the trip also collapsed the board - it froze
mid-test, `/data` needed ext4 recovery, and the next boot reported
`throttled=0x50005`, under-voltage live. At idle it settles back to `0x50000`,
which is only the sticky record of what happened.

That load is nothing like the real workload, which is an incremental rsync over
WiFi. The fan trip could have been verified by simply waiting for a warm
afternoon, or by dropping the trip temp to just under the idle temperature. Peak
current on a supply this marginal is not a thing to spend carelessly.

## sshd accepts and closes, with nothing in the log: reseat the card

The symptom, twice:

```
kex_exchange_identification: Connection closed by remote host
```

TCP connects, sshd closes before sending its version string, and it does this
to every client - so it is not a per-source block. Meanwhile the unit answers
ping, `tailscale ping` returns a pong, and the tailnet shows it online. The
journal records nothing at all: the previous boot's `ssh` log simply ends, with
no error, no restart, no crash.

**It was the SD card needing to be reseated**, after the board was disturbed
fitting a case.

The mechanism fits exactly, and it explains the silence. sshd forks a child per
connection and reads its host keys from disk each time, so it fails the moment
the card stops answering. Daemons already resident - tailscaled, the kernel
network stack - keep running from RAM and look perfectly healthy. And the
journal cannot record the cause, because writing the journal needs the same
card.

That last part is the trap. An empty log is not evidence that nothing went
wrong; on a storage fault it is exactly what you should expect. This hypothesis
was raised early, then dropped *because* the post-mortem showed no card errors -
which was the one piece of evidence that could not have appeared either way.

Before theorising about sshd, reseat the card.

## Check what is actually supplying the board before reading the flags

A Pi 4 wants roughly 1.2 A through the boot surge. A PC USB port gives 500 mA
(USB 2.0) or 900 mA (USB 3.0), so a unit moved to one after a charger dies will
show live under-voltage at idle:

```
throttled=0x50005    bit0 under-voltage NOW, bit2 throttled NOW,
                     bits 16/18 the sticky record since boot
```

Sticky bits clear only on reboot, so a clean `0x0` after a reboot is the test
that a replacement supply is actually adequate.

Worth stating plainly because two failures here had unrelated, mundane causes -
a card that needed reseating and a charger that died - while the fan happened to
be connected for both. That was enough to suggest a pattern that did not exist.
Two coincidences are not a trial.

## The pointer is not what the car is reading

The sync chose its target from `/data/state/active`:

```sh
ACTIVE=$(cat "$STATE/active")
case "$ACTIVE" in A) TARGET=B ;; B) TARGET=A ;; esac
```

That is right exactly once per boot. `carmp3-gadget-active` reads the pointer at
boot and binds that image, so at that moment pointer and bound image agree - but
the sync **flips the pointer when it finishes**. From then on they disagree, and
a second sync in the same boot resolves its target to the image the gadget is
currently serving. It writes into the volume the car is reading, which is the
one thing the A/B split exists to prevent.

Observed: `pointer=B`, gadget bound `B.img`, and the log cheerfully reporting
`car is playing A, writing into B`.

The pointer is a statement about the *next* boot. The authoritative answer to
"what is the car reading right now" is the gadget itself:

```sh
LUN=/sys/kernel/config/usb_gadget/carmp3/functions/mass_storage.0/lun.0/file
BOUND=$(cat "$LUN" 2>/dev/null); BOUND=${BOUND##*/}; BOUND=${BOUND%.img}
case "$BOUND" in
  A|B) ACTIVE=$BOUND ;;
  *)   ACTIVE=$(cat "$STATE/active" 2>/dev/null) ;;   # gadget not up yet
esac
```

Normal operation never reached this, which is why it survived so long. The
retry path is guarded by `/run/carmp3-sync.ok` and only runs when the first
sync *failed* - and a failed sync does not flip the pointer, so the retry
recomputes the same correct target. It takes a manual `systemctl restart
carmp3-sync` to get there.

Two things made it harmless when it did fire: no USB host was attached
(`/sys/class/udc/*/state` said `not attached`, the port in use was power-only),
and the sync still fsck'd the image before flipping. Both images came out
byte-identical and clean. That is luck, not design.

Verified after the fix on a unit whose gadget held `A.img` with the pointer
already flipped to `B` - the exact state that used to mis-resolve. It chose B.

## Retire the netplan connections once a real profile exists

Writing a persistent profile does not stop netplan rebuilding its own copy into
`/run` on every boot, and NetworkManager keeps activating that one - so the
volatile connection stays in charge and the persistent file is only a spare.
Remove the generated YAML from the persistent layer to finish the job:

```sh
cp -a /media/root-ro/etc/netplan/. /data/netplan-backup/
mount -o remount,rw /media/root-ro
rm -f /media/root-ro/etc/netplan/90-NM-*.yaml
mount -o remount,ro /media/root-ro
```

Prove the replacement works *before* removing anything, and do it with a way
back. Activating the persistent profile from a detached job, with a reboot
armed to fire a few minutes later unless cancelled, makes the failure mode a
reboot rather than a unit that has to be fetched:

```sh
systemd-run --unit=netrevert --on-active=180 systemctl reboot
systemd-run --unit=netswitch --on-active=3 nmcli con up Skywalker
# reconnect, confirm, then: systemctl stop netrevert.timer
```

The reboot restores the working state precisely because the netplan files are
still there at that point. Delete them only after the profile has carried a
session.

`NetworkManager.service` dropped from 9.549s to 5.729s afterwards, but total
boot did not move (35.0s -> 35.4s), so the change looked worthless.

It was not. That measurement was taken while the unit was under-volting on an
inadequate supply; a throttled board makes every boot timing noise. Repeated on
a proper charger:

| | supply | total | NetworkManager |
|---|---|---|---|
| after cloud-init fix | good | 35.048s | 9.549s |
| after netplan cleanup | browning out | 35.356s | 5.729s |
| after netplan cleanup | **good** | **28.869s** | **3.687s** |

The cleanup was worth about 6 seconds, and NetworkManager fell to roughly what
the hand-built unit does (3.0s). Fix the power before drawing any conclusion
from a boot chart - the brownout hid a real improvement completely.
