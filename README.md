# xiaomi-openwrt-install

**English** · [Русская инструкция: Windows 10 и установка по Wi-Fi](README.ru.md)

Install OpenWrt on a supported Xiaomi router without UART or soldering, or
return an installed router to an approved stock image.

The installer uses the [documented V1 → V2](https://github.com/ADCDS/xiaomi-ax3000t-cabmeshd-disclosure#combined-v1--v2-capuci-exploit) `cab_meshd` chain to obtain root,
checks the exact board, NAND and partition layout, downloads the OpenWrt images
selected by the device profile, boots the RAM installer, and writes the
permanent image.

> Read [NOTICE](NOTICE) before continuing. Run this only on a router you own.

## Before starting

You need a Linux or Windows 10/11 computer with Python 3.9 or newer.
For Linux, install these tools:

```sh
sudo apt install sshpass openssl
```

Factory-reset the router and leave it at Xiaomi's setup wizard. **Do not finish
the web wizard.** Connect the computer to a LAN port or to the router's open
factory Wi-Fi network. Keep the router on stable power.

Use an isolated link. If using factory Wi-Fi, ensure no other client is joined.
The temporary root callback uses a per-run token and peer checks, while the
stock firmware cannot provide an authenticated encrypted channel for this
exploit stage.

Clone this repository:

```sh
git clone https://github.com/alexsys87/xiaomi-openwrt-install.git
cd xiaomi-openwrt-install
```

### Windows 10/11 (native, without WSL)

Install Python from [python.org](https://www.python.org/downloads/windows/),
including the **Python launcher (`py`)**. Download this repository's ZIP and
extract it, or clone it as above. Open PowerShell in the extracted folder:

```powershell
py -3 -m pip install -r requirements-windows.txt
py -3 install.py --list-interfaces
py -3 install.py standard --interface "Ethernet"
```

Use the adapter name shown by `--list-interfaces` (including spaces), or its
numeric index. For example, `--interface "Wi-Fi"` or `--interface 12`.
Localized names such as `"Беспроводная сеть"` are supported. You may omit
`--interface` to use the adapter selected by Windows' route to the stock
router, normally `192.168.31.1`. `install.cmd standard --interface "Ethernet"`
is an equivalent launcher from Command Prompt; in PowerShell use
`./install.cmd standard --interface "Ethernet"`.

PowerShell 5.1 (included with Windows 10) provides adapter/address information;
Linux `ip`, `nmcli`, `sshpass`, and a separate OpenSSL installation are not
needed. Python dependencies provide password SSH, legacy SCP (no router SFTP
required), and root-password hashing. Keep IPv6 enabled on the selected
adapter: rediscovery after reboot uses scoped IPv6 link-local addresses.
Join the displayed temporary Wi-Fi manually in Windows when prompted, then
the permanent Wi-Fi after installation; this script does not change Windows
Wi-Fi profiles automatically.

If the network is not found or the callback never arrives:

1. Connect to a **LAN** port or the factory Wi-Fi, not the WAN port. Check that
   the adapter has a `192.168.31.x` IPv4 address with `--list-interfaces` and that
   `http://192.168.31.1` opens, but **do not complete** the Xiaomi wizard.
2. Disconnect VPNs and competing links with overlapping subnets. An explicit
   adapter cannot override an incorrect Windows route; the installer rejects
   a route/adapter mismatch before consuming the one-shot trigger.
3. Allow the actual `python.exe` through Windows Defender Firewall for inbound
   TCP **8000, 4444, 4445** from the stock router. Factory links may be classified
   as Public, so a Private-only exception may not work. Do not disable the
   firewall globally. For a narrowly scoped rule, run an elevated PowerShell:

   ```powershell
   $python = py -3 -c "import sys; print(sys.executable)"
   New-NetFirewallRule -DisplayName "Xiaomi OpenWrt installer" -Direction Inbound -Action Allow -Program $python -Protocol TCP -LocalPort 8000,4444,4445 -RemoteAddress 192.168.31.1 -Profile Any
   # Remove the temporary rule when finished:
   Get-NetFirewallRule -DisplayName "Xiaomi OpenWrt installer" | Remove-NetFirewallRule
   ```

   If using `--host` or custom callback ports, adjust the rule accordingly.

Run the installer as your normal user; elevation is only needed to add a
firewall rule. Store backups, configuration archives and `resume.json` in a
private, non-shared NTFS folder: Windows permissions are inherited from that
folder, not POSIX `chmod`. These files contain device data and session secrets.
The host-key policy is unchanged: run only on your isolated, trusted router
link. Automated Windows tests use a hosted Windows runner. A complete native
Windows 10 Wi-Fi installation was confirmed on physical Xiaomi AX3000T RD03v2
hardware on 2026-10-11: stock ROM 2.0.28 → standard OpenWrt release v1.14.
The final verification reported `board=xiaomi,mi-router-ax3000t-v2`,
`rootfs_type=overlay`, and `OpenWrt is installed on NAND.` See the
[Russian walkthrough and troubleshooting notes](README.ru.md) for this run.
This confirms that particular installation, not every device or firmware variant.

## Install OpenWrt

Choose one image:

```sh
# Standard image (recommended)
python3 install.py standard

# Experimental NSS acceleration
python3 install.py nss
```

By default, the installer selects the latest published release from the device
profile's firmware repository, excluding drafts and prereleases. It downloads
and verifies the matching RAM and permanent images, detects whether the router
is connected by Ethernet or Wi-Fi, backs up device-specific partitions, and asks
before each irreversible write. New firmware releases become the default
automatically.

Override connection detection with `--transport wired` or `--transport wifi`.
If route-based interface detection fails, append `--interface INTERFACE_NAME`.

During a Wi-Fi install, the router temporarily reboots into a RAM installer
network. `install.py` prints its SSID and password before the reboot and waits
while you join it. By default, final verification uses that same Wi-Fi
interface, so before doing anything to the router the installer requires the
SSID, passphrase, and country code for the permanent system. An interactive run
prompts for missing values; automation must pass `--wifi-ssid`, `--wifi-key`,
and `--wifi-country`. After `sysupgrade` makes the temporary network disappear,
join that permanent SSID. The installer never prints its passphrase.

To keep permanent Wi-Fi disabled, select an Ethernet interface for the final
check and connect it when the installer asks:

```sh
python3 install.py standard --transport wifi --interface WIFI_INTERFACE \
    --verify-interface ETHERNET_INTERFACE
```

Both paths rediscover the router and verify the board, persistent overlay, and
partition sizes.

The temporary credentials are fixed and public, and root has no password in
the RAM system. Keep the link isolated until the permanent system has booted.

When final verification is over Ethernet, omitting configuration arguments
leaves OpenWrt Wi-Fi disabled and the root password unset. Configure both
interactively during installation with:

```sh
python3 install.py standard --configure
```

Download and fully verify the latest release's image set without contacting a
router:

```sh
python3 install.py standard --dry-run
```

Use `--release TAG` to select a particular release. `--release latest` explicitly
selects the default behavior. Each installation records the resolved tag and
image hashes in its private resume manifest, so continuing an interrupted run
keeps the original release even if a newer one has been published.

Offline installs require an explicit tag and its verified cached images:

```sh
python3 install.py standard --release v1.13 --dry-run
python3 install.py standard --release v1.13 --offline
```

If GitHub is unavailable, an explicitly selected tag can also use its verified
cache. Selecting `latest` requires GitHub access.

If the connection is lost after `sysupgrade` starts, do not repeat the flash
stage. Use the private manifest printed by the original run to perform only
the final checks, optionally over a different interface:

```sh
python3 install.py --verify-only /path/to/resume.json \
    --verify-interface INTERFACE_NAME
```

## Return to stock

Download an official stock image whose SHA-256 is approved by the selected
device profile. The [technical reference](docs/technical.md#getting-the-images)
lists the approved images for the enabled profiles.

From an installed OpenWrt system, run:

```sh
nmcli device status
IFACE=YOUR_ROUTER_INTERFACE
python3 revert.py --device rd03v2 /path/to/approved-stock-image.bin \
    --interface "$IFACE"
```

`revert.py` verifies the stock image, downloads and verifies the profile's RAM
image pair, pivots the router into RAM, restores stock, and confirms that the
setup wizard returns. It infers Ethernet or Wi-Fi from the selected interface.
The RAM images default to the latest published release; use `--release TAG` to
select a specific release, and add `--offline` to use its verified cache.
During a Wi-Fi revert, join the temporary RAM network when `revert.py` prints
its SSID and password. The useful overrides are:

```sh
python3 revert.py --device rd03v2 /path/to/approved-stock-image.bin \
    --transport wired --interface "$IFACE"

python3 revert.py --device rd03v2 /path/to/approved-stock-image.bin \
    --interface "$IFACE" --root-password 'YOUR_OPENWRT_PASSWORD'

python3 revert.py --device rd03v2 /path/to/approved-stock-image.bin \
    --interface "$IFACE" --dry-run
```

Use `revert.py` for normal restoration. `restore.py` is the lower-level writer
and refuses to run unless the router is already booted from a RAM initramfs.

## Supporting tools

| Command | Purpose |
|---|---|
| `python3 release.py --device rd03v2` | Inspect the latest release and its required assets. |
| `python3 release.py --device rd03v2 --download --wifi --dest images` | Download and verify a release image set. |
| `python3 probe.py --device rd03v2` | Collect hardware and flash-layout evidence without raw firmware-partition writes. It initializes or reboots stock as needed, modifies stock control state, and consumes the one-shot trigger. |
| `python3 attach.py --bind OPERATOR_ADDRESS --peer ROUTER_ADDRESS --session-token TOKEN 'id'` | Reattach to the authenticated callback from an interrupted run. |
| `python3 restore.py --device rd03v2 /path/to/approved-stock-image.bin --discover INTERFACE_NAME` | Run the low-level stock writer from an already-booted RAM system. |
| `python3 ubiparse.py /path/to/ubi-dump.bin` | Inspect a raw UBI dump offline. |
| `python3 selftest.py` | Run checks that do not require a router. |

Windows: replace `python3` with `py -3`. Run the cross-platform Windows
regression suite with `py -3 -m unittest -v test_hostnet test_windows`;
`selftest.py` also contains Linux/BusyBox-specific integration checks.
`probe.py`'s optional automatic Wi-Fi reconnect remains Linux-only; join
the displayed network manually on Windows.

See the [technical reference](docs/technical.md) for interrupted runs, manual
stages, stock-image hashes, flash-layout details, and recovery procedures.

## Supported device

| Profile | Hardware | Install source | Return to stock |
|---|---|---|---|
| `rd03v2` | Xiaomi AX3000T RD03v2, Qualcomm IPQ5018 | Stock 2.0.28, hardware-tested | 2.0.28 hardware-tested; 2.0.12 hash-recognized but not hardware-tested |

The exact tested stock ROMs, release source, NAND support, layouts, and accepted
stock-image hashes live in [`devices.py`](devices.py). Other Xiaomi models may
contain the same vulnerabilities, but their flash and boot layouts are not
interchangeable. The installer rejects every model without a reviewed and
hardware-tested profile.

Technical details and the vulnerability disclosure are in
[`xiaomi-ax3000t-cabmeshd-disclosure`](https://github.com/ADCDS/xiaomi-ax3000t-cabmeshd-disclosure).

## License

GPL-2.0-only. See [LICENSE](LICENSE).
