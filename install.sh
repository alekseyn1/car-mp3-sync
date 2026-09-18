#!/bin/bash
# Build a car-mp3-sync unit from a fresh Raspberry Pi OS Lite install.
#
# Run repeatedly: it works out what is already done, does the next thing, and
# tells you when a reboot is needed. Several steps cannot be combined because
# they need the kernel to come back with a different view of the disk.
#
#   sudo ./install.sh
#
# Configure by environment or by editing the block below:
#   UNIT_NAME=mp3drive-sfax NAS_HOST_LAN=192.168.10.5 sudo -E ./install.sh
set -uo pipefail

UNIT_NAME="${UNIT_NAME:-mp3drive}"
NAS_HOST_LAN="${NAS_HOST_LAN:-192.168.10.5}"
NAS_HOST_VPN="${NAS_HOST_VPN:-}"
NAS_SHARE="${NAS_SHARE:-Storage}"
NAS_SUBPATH="${NAS_SUBPATH:-Music/Youtube/Output}"
ROOT_SIZE_SECTORS="${ROOT_SIZE_SECTORS:-16777216}"   # 8 GiB
IMAGE_SIZE="${IMAGE_SIZE:-8G}"
BOOTFW=/boot/firmware
SRC="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
reboot_needed() { printf '\n\033[1;33m>> reboot, then run this script again\033[0m\n'; exit 0; }

# ---------------------------------------------------------------- stage 1
say "USB peripheral mode"
CHANGED=0
if ! grep -q 'dr_mode=peripheral' "$BOOTFW/config.txt"; then
  printf '\n# present the USB-C / OTG port as a device\ndtoverlay=dwc2,dr_mode=peripheral\n' >> "$BOOTFW/config.txt"
  note "config.txt: added dwc2 overlay"; CHANGED=1
fi
# cmdline.txt is ONE line; the parameter must be appended to it, not added below
if ! grep -q 'modules-load=dwc2' "$BOOTFW/cmdline.txt"; then
  sed -i '1s|$| modules-load=dwc2|' "$BOOTFW/cmdline.txt"
  note "cmdline.txt: added modules-load=dwc2"; CHANGED=1
fi
[ "$CHANGED" = 1 ] && reboot_needed
[ -n "$(ls /sys/class/udc 2>/dev/null)" ] || { echo "no UDC - dwc2 did not load"; exit 1; }
note "UDC present: $(ls /sys/class/udc)"

# ---------------------------------------------------------------- stage 2
say "packages"
MISSING=""
for p in fatsort cifs-utils dosfstools rsync; do
  dpkg -s "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"
done
if [ -n "$MISSING" ]; then
  note "installing:$MISSING"
  apt-get update -qq && apt-get install -y -qq $MISSING
fi
note "all present"

# ---------------------------------------------------------------- stage 3
say "/data partition"
if [ ! -b /dev/mmcblk0p3 ]; then
  CUR=$(cat /sys/block/mmcblk0/mmcblk0p2/size)
  if [ "$CUR" -gt "$ROOT_SIZE_SECTORS" ]; then
    note "root is $CUR sectors, shrinking to $ROOT_SIZE_SECTORS via initramfs"
    install -m755 "$SRC/pi/initramfs/hooks/carmp3shrink" /etc/initramfs-tools/hooks/
    install -m755 "$SRC/pi/initramfs/scripts/local-premount/carmp3shrink" \
                  /etc/initramfs-tools/scripts/local-premount/
    update-initramfs -u -k "$(uname -r)"
    note "the shrink runs before root mounts, then reboots itself once more"
    reboot_needed
  fi
  END=$(( $(blockdev --getsz /dev/mmcblk0) - 1 ))
  START=$(( 1064960 + ROOT_SIZE_SECTORS ))
  note "creating p3 from sector $START to $END"
  # parted, not sfdisk --append: the latter silently failed to persist here,
  # and parted also gets the running kernel to see the new partition
  parted -s /dev/mmcblk0 unit s mkpart primary ext4 "${START}s" "${END}s"
  sleep 2
  mkfs.ext4 -F -L cardata /dev/mmcblk0p3
fi
PARTUUID=$(blkid -s PARTUUID -o value /dev/mmcblk0p3)
# NOT fstab: overlayroot scans fstab and would overlay /data into tmpfs
sed -e "s|@PARTUUID@|$PARTUUID|" "$SRC/pi/systemd/data.mount" > /etc/systemd/system/data.mount 2>/dev/null \
  || install -m644 "$SRC/pi/systemd/data.mount" /etc/systemd/system/data.mount
sed -i "s|^What=.*|What=/dev/disk/by-partuuid/$PARTUUID|" /etc/systemd/system/data.mount
sed -i '\| /data |d' /etc/fstab
systemctl daemon-reload
systemctl enable --now data.mount
mkdir -p /data/images /data/state /data/log /data/tailscale
chmod 700 /data/tailscale
note "/data: $(findmnt -no SOURCE,SIZE /data)"

# ---------------------------------------------------------------- stage 4
say "scripts, units and triggers"
install -m755 "$SRC"/pi/bin/carmp3-* /usr/local/sbin/
install -m644 "$SRC"/pi/systemd/carmp3-*.service /etc/systemd/system/
install -m644 "$SRC"/pi/systemd/carmp3-*.timer   /etc/systemd/system/ 2>/dev/null
install -d -m755 /etc/NetworkManager/dispatcher.d
install -o root -g root -m755 "$SRC/pi/networkmanager/90-carmp3-sync" \
        /etc/NetworkManager/dispatcher.d/
install -d /etc/systemd/system/tailscaled.service.d
install -m644 "$SRC/pi/systemd/dropins/tailscaled-state.conf" \
        /etc/systemd/system/tailscaled.service.d/state.conf
install -d /etc/systemd/system/carmp3-sync.service.d
install -m644 "$SRC/pi/systemd/dropins/carmp3-sync-tailscale.conf" \
        /etc/systemd/system/carmp3-sync.service.d/tailscale.conf
systemctl daemon-reload
systemctl enable carmp3-gadget carmp3-sync carmp3-beep-init carmp3-net-trim >/dev/null 2>&1
# --now so the timer is live immediately, not only after the next reboot
systemctl enable --now carmp3-retry.timer >/dev/null 2>&1
note "installed and enabled"

# ---------------------------------------------------------------- stage 5
say "configuration"
[ -f /etc/carmp3.conf ] || cat > /etc/carmp3.conf <<CONF
NAS_HOST_LAN=$NAS_HOST_LAN
NAS_HOST_VPN=$NAS_HOST_VPN
NAS_SHARE=$NAS_SHARE
NAS_SUBPATH=$NAS_SUBPATH
CRED=/etc/carmp3-nas.cred
VFAT_OPTS=uid=1000,gid=1000,utf8=1,codepage=866
CONF
note "/etc/carmp3.conf"
[ -f /etc/carmp3-nas.cred ] || {
  printf 'username=\npassword=\n' > /etc/carmp3-nas.cred
  chmod 600 /etc/carmp3-nas.cred; chown root:root /etc/carmp3-nas.cred
  note "!! fill in /etc/carmp3-nas.cred with a READ-ONLY NAS account"
}
if [ "$(hostname)" != "$UNIT_NAME" ]; then
  OLD=$(hostname)
  hostnamectl set-hostname "$UNIT_NAME"
  # /etc/hosts must follow, or every sudo waits on a failed DNS lookup for a
  # name nothing can resolve
  if grep -qE "^127\.0\.1\.1" /etc/hosts; then
    sed -i -E "s|^(127\.0\.1\.1[[:space:]]+).*|$UNIT_NAME|" /etc/hosts
  else
    printf '127.0.1.1	%s
' "$UNIT_NAME" >> /etc/hosts
  fi
  note "hostname $OLD -> $UNIT_NAME (and /etc/hosts)"
fi

# ---------------------------------------------------------------- stage 6
say "images"
for IMG in A B; do
  P="/data/images/$IMG.img"
  [ -f "$P" ] && { note "$IMG exists"; continue; }
  truncate -s "$IMAGE_SIZE" "$P"            # sparse
  parted -s "$P" mklabel msdos mkpart primary fat32 1MiB 100%
  L=$(losetup -fP --show "$P"); mkfs.vfat -F 32 -n MUSIC "${L}p1" >/dev/null; losetup -d "$L"
  note "$IMG created"
done
[ -f /data/state/active ] || echo A > /data/state/active

cat <<DONE

Remaining, by hand because they need secrets or a decision:

  1. NAS credentials   -> /etc/carmp3-nas.cred (read-only account)
  2. WiFi              -> nmcli connection add ... for each network the car sees
  3. Tailscale         -> curl -fsSL https://tailscale.com/install.sh | sh
                          tailscale up --hostname=$UNIT_NAME --accept-dns=false
                          (state already points at /data, so it survives reboots)
  4. Seed on the LAN   -> /usr/local/sbin/carmp3-sync
  5. Read-only root    -> raspi-config nonint do_overlayfs 0   [LAST]

DONE
