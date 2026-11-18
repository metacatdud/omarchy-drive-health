import QtQuick
import Quickshell.Io
import qs.Ui
import qs.Commons
import qs.Commons as Commons

Column {
  id: root
  property string driveId: ""
  property string driveName: ""
  property bool demo: false
  readonly property bool busy: writer.running
  property string errorCode: ""
  property bool saveSucceeded: false
  // Test harnesses can supply a temporary config without changing user settings.
  property string configPath: ""
  property alias warrantyUntil: endDate.text
  property alias ratedTbw: allowance.text
  signal saved()
  signal cancelled()
  spacing: Style.space(12)

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }
  function load(drive) {
    driveId = drive.id
    driveName = drive.model
    var warranty = drive.warranty || ({})
    endDate.text = warranty.expires || ""
    allowance.text = warranty.rated_tbw ? String(warranty.rated_tbw) : ""
    errorCode = ""
    Qt.callLater(function() { endDate.forceActiveFocus() })
  }
  function save() {
    if (busy || demo) return
    errorCode = ""
    saveSucceeded = false
    writer.command = ["python3", "-I", decodeURIComponent(Qt.resolvedUrl("backend/drive_health.py").toString().replace(/^file:\/\//, "")),
      "--save-warranty", driveId, "--warranty-until=" + endDate.text, "--rated-tbw=" + allowance.text]
      .concat(configPath ? ["--config", configPath] : [])
    writer.running = true
  }
  Keys.onEscapePressed: if (!busy) cancelled()

  Label { text: strings.t("settingsTitle"); font.bold: true; font.pixelSize: Style.font.subtitle }
  Label { text: root.driveName; opacity: 0.7 }
  Label { text: strings.t("settingsHelp"); font.pixelSize: Style.font.caption; opacity: 0.65 }
  Label { text: strings.t("settingsDate") }
  TextField {
    id: endDate
    width: root.width
    placeholderText: "YYYY-MM-DD"
    maximumLength: 10
    enabled: !root.busy
    Accessible.name: strings.t("settingsDate")
    onAccepted: allowance.forceActiveFocus()
  }
  Label { text: strings.t("settingsTbw") }
  TextField {
    id: allowance
    width: root.width
    placeholderText: strings.t("settingsTbwExample")
    maximumLength: 32
    enabled: !root.busy
    Accessible.name: strings.t("settingsTbw")
    onAccepted: root.save()
  }
  Label { visible: root.demo; text: strings.t("settingsDemo"); opacity: 0.7 }
  Label { visible: root.errorCode !== ""; text: strings.t("settingsError." + root.errorCode); color: Commons.Color.urgent }
  Row {
    width: root.width
    spacing: Style.space(8)
    Button {
      text: root.busy ? strings.t("settingsSaving") : strings.t("settingsSave")
      width: (parent.width - parent.spacing) / 2
      focusable: true
      bordered: true
      active: true
      enabled: !root.busy && !root.demo
      onClicked: root.save()
    }
    Button {
      text: strings.t("settingsCancel")
      width: (parent.width - parent.spacing) / 2
      focusable: true
      bordered: true
      enabled: !root.busy
      onClicked: root.cancelled()
    }
  }
  Process {
    id: writer
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var response = JSON.parse(text)
          root.saveSucceeded = response.ok === true
          root.errorCode = root.saveSucceeded ? "" : (response.error || "write_failed")
        } catch (error) { root.errorCode = "write_failed" }
      }
    }
    onExited: function(code) {
      if (code !== 0) root.errorCode = "write_failed"
      else if (root.saveSucceeded) Qt.callLater(function() { root.saved() })
    }
  }
  component Label: Text {
    width: root.width
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    color: Commons.Color.foreground
    font.family: Style.font.family
    font.pixelSize: Style.font.body
  }
}
