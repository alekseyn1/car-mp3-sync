# Build guide

Tested on Raspberry Pi OS Lite (Debian 13 trixie, kernel 6.18) on a Pi 4.
The same card boots a Zero 2 W unchanged — see *Moving to a Zero 2 W* below.

Read [findings.md](findings.md) first if something behaves oddly; most of the
surprises in this build are catalogued there.

## 1. Flash and enable peripheral mode

Flash Raspberry Pi OS Lite with SSH and WiFi configured. Then:

```bash
echo 'dtoverlay=dwc2,dr_mode=peripheral' | sudo tee -a /boot/firmware/config.txt
sudo sed -i '1s|$| modules-load=dwc2|' /boot/firmware/cmdline.txt
sudo reboot
```

`cmdline.txt` is a **single line** — `modules-load=dwc2` must be appended to it,
space-separated, not placed on a new line.

`dr_mode=peripheral` matters. Without it the port negotiates its role from the
cable and can come up as a *host*: powered, but never enumerating.

After the reboot:

```bash
ls /sys/class/udc     # fe980000.usb on a Pi 4, 20980000.usb on a Zero 2 W
```

An empty result means the overlay did not take and nothing past here will work.

## 2. Make room for `/data`

The installer expands root to fill the card, and ext4 cannot shrink while
mounted. `pi/initramfs/` contains a script that runs before root is mounted,
shrinks it, and reboots. It is idempotent — it compares the current partition
size against the target and exits early — so it is safe to leave installed.

```bash
sudo cp pi/initramfs/hooks/carmp3shrink /etc/initramfs-tools/hooks/
sudo cp pi/initramfs/scripts/local-premount/carmp3shrink /etc/initramfs-tools/scripts/local-premount/
sudo chmod +x /etc/initramfs-tools/hooks/carmp3shrink \
              /etc/initramfs-tools/scripts/local-premount/carmp3shrink
sudo update-initramfs -u -k "$(uname -r)"
sudo reboot
```

Watch it with `dmesg | grep carmp3`. Then create the new partition in the freed
space and mount it:

```bash
sudo parted -s /dev/mmcblk0 unit s mkpart primary ext4 <start>s <end>s
sudo mkfs.ext4 -F -L cardata /dev/mmcblk0p3
sudo cp pi/systemd/data.mount /etc/systemd/system/data.mount   # edit the PARTUUID
sudo systemctl daemon-reload && sudo systemctl enable --now data.mount
sudo mkdir -p /data/images /data/state /data/log
```

Take `<start>`, `<end>` and the PARTUUID from `sudo sfdisk -F /dev/mmcblk0`,
`sudo sfdisk -d /dev/mmcblk0` and `sudo blkid /dev/mmcblk0p3`.

**Do not put `/data` in `/etc/fstab`.** `overlayroot` — enabled in step 6 —
scans fstab and would overlay this partition into RAM, silently discarding every
sync. A systemd mount unit is invisible to it. See
[findings.md](findings.md).

## 3. Create the images

Sparse, so 8 GiB apparent costs only what is written:

```bash
for img in A B; do
  truncate -s 8G /data/images/$img.img
  sudo parted -s /data/images/$img.img mklabel msdos mkpart primary fat32 1MiB 100%
  LOOP=$(sudo losetup -fP --show /data/images/$img.img)
  sudo mkfs.vfat -F 32 -n MUSIC ${LOOP}p1
  sudo losetup -d $LOOP
done
echo A | sudo tee /data/state/active
```

Expose the **whole image including its MBR** as the backing file, not the
partition — some head units refuse a filesystem with no partition table.

## 4. NAS access

Create a **dedicated read-only account** rather than using your own. On
Synology:

```bash
sudo synouser  --add carmp3 '<generated-password>' "Car MP3 sync" 0 "" 0
sudo synoshare --setuser <Share> RO + carmp3
```

The account gets `/sbin/nologin`, so the worst case if someone reads the SD card
is read-only access to one share.

```bash
sudo tee /etc/carmp3-nas.cred >/dev/null <<'CRED'
username=carmp3
password=<generated-password>
CRED
sudo chmod 600 /etc/carmp3-nas.cred
sudo chown root:root /etc/carmp3-nas.cred
```

If you would rather use rsync over SSH than SMB, note that Synology ships a
setuid-root rsync that refuses `--server` mode unless the rsync service is
enabled in Control Panel → File Services. The failure is misleading: SSH
authenticates fine, then the far side answers `rsync service is no running`.
Leave *Enable rsync account* off — that is unencrypted rsyncd on port 873 and
nothing here uses it.

## 5. Install

```bash
sudo apt install -y fatsort cifs-utils dosfstools
sudo cp pi/bin/* /usr/local/sbin/ && sudo chmod +x /usr/local/sbin/carmp3-*
sudo cp pi/systemd/*.service /etc/systemd/system/
sudo cp pi/carmp3.conf.example /etc/carmp3.conf    # edit to taste
sudo systemctl daemon-reload
sudo systemctl enable carmp3-gadget carmp3-sync carmp3-beep-init carmp3-net-trim
```

Seed the first sync indoors — several gigabytes will not transfer in the minute
you spend in WiFi range backing out of the driveway:

```bash
sudo /usr/local/sbin/carmp3-sync
```

Then reboot and check:

```bash
systemctl is-active carmp3-gadget
cat /sys/kernel/config/usb_gadget/carmp3/functions/mass_storage.0/lun.0/file
tail /data/log/carmp3-sync.log
```

## 6. Read-only root — last

Only once everything works. `raspi-config` → Performance Options → Overlay File
System. Everything that must persist already lives on `/data`, which stays
writable.

Do this last. Debugging a read-only root is miserable, and every ignition-off is
an unclean power cut — which is exactly what it exists to survive.

## Moving to a Zero 2 W

Raspberry Pi OS images carry firmware for every model, so the same card boots
both. Split any board-specific tuning into `[pi4]` / `[pi02]` sections in
`config.txt`, re-measure boot-to-enumeration, and remember the rewiring: the
Zero has two micro-USB ports, and in the car power *and* data both come through
the one marked **`USB`** while `PWR IN` stays empty. On the bench you would
naturally power from `PWR IN` instead, which is an easy thing to get backwards
when installing.
