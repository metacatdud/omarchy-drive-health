# Local prototype build: run tools/package to stage these sources and build.
pkgname=omarchy-drive-health-collector
pkgver=0.4.0
pkgrel=1
pkgdesc='Read-only local SMART collector for the Omarchy Drive Health plugin'
arch=('any')
license=('MIT')
depends=('python' 'smartmontools' 'systemd')
source=('drive_health.py' 'omarchy-drive-health.service' 'omarchy-drive-health.timer' 'LICENSE')
# tools/package replaces these with checksums for the staged release files.
sha256sums=('SKIP' 'SKIP' 'SKIP' 'SKIP')

package() {
  install -Dm644 drive_health.py "$pkgdir/usr/lib/omarchy-drive-health/drive_health.py"
  install -Dm644 omarchy-drive-health.service "$pkgdir/usr/lib/systemd/system/omarchy-drive-health.service"
  install -Dm644 omarchy-drive-health.timer "$pkgdir/usr/lib/systemd/system/omarchy-drive-health.timer"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
