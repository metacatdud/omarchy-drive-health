PREFIX ?= /usr
DESTDIR ?=

.PHONY: test install-backend
test:
	python3 -m unittest discover -s tests -v
	omarchy plugin validate .

install-backend:
	install -Dm644 backend/drive_health.py $(DESTDIR)$(PREFIX)/lib/omarchy-drive-health/drive_health.py
	install -Dm644 systemd/omarchy-drive-health.service $(DESTDIR)$(PREFIX)/lib/systemd/system/omarchy-drive-health.service
	install -Dm644 systemd/omarchy-drive-health.timer $(DESTDIR)$(PREFIX)/lib/systemd/system/omarchy-drive-health.timer
