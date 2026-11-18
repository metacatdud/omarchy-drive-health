import QtQuick
import qs.Ui
import qs.Commons
import qs.Commons as Commons

Column {
  id: root
  required property var report
  property color foreground: Commons.Color.foreground
  property string fontFamily: Style.font.family
  property string selectedId: ""
  property bool editing: false
  property bool settingsWereSaved: false
  signal settingsSaved()
  function openSettings() {
    if (!drives.length) return
    editing = true
    settingsWereSaved = false
    editor.load(drive)
  }
  function cancelSettings() { if (!editor.busy) editing = false }

  readonly property var drives: report.drives || []
  readonly property int selectedIndex: Math.max(0, drives.findIndex(function(d) { return d.id === root.selectedId }))
  readonly property var drive: drives[selectedIndex] || ({})
  readonly property var warranty: drive.warranty || ({})
  spacing: Style.space(14)

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }
  function value(n, decimals) {
    return typeof n === "number" && isFinite(n) ? Commons.I18n.formatNumber(n, decimals || 0) : strings.t("unknown")
  }
  function percent(n) { return typeof n === "number" ? value(n) + "%" : strings.t("unknown") }
  function date(iso) {
    return new Date(iso + "T12:00:00").toLocaleDateString(Qt.locale(Commons.I18n.formatLocale), Locale.ShortFormat)
  }
  function observationTime(timestamp) {
    return new Date(timestamp * 1000).toLocaleString(Qt.locale(Commons.I18n.formatLocale), Locale.ShortFormat)
  }
  function observationText(detail) {
    switch (detail.observation) {
      case "baseline": return strings.t("observation.baseline").arg(observationTime(detail.baseline_at))
      case "increased": return strings.t("observation.increased").arg(value(detail.delta)).arg(observationTime(detail.comparison_at))
      case "unchanged": return strings.t("observation.unchanged").arg(observationTime(detail.comparison_at))
      case "decreased": return strings.t("observation.decreased").arg(observationTime(detail.comparison_at))
      default: return strings.t("observation.flag").arg(observationTime(detail.first_observed_at))
    }
  }
  function cycleDrive(delta) {
    if (drives.length) selectedId = drives[(selectedIndex + delta + drives.length) % drives.length].id
  }

  Row {
    width: root.width
    spacing: Style.space(12)
    Label { width: Style.space(28); text: "󰋊"; font.pixelSize: Style.font.display; anchors.verticalCenter: parent.verticalCenter }
    Column {
      width: parent.width - Style.space(40)
      spacing: Style.space(3)
      Label { width: parent.width; text: strings.t("title"); font.bold: true; font.pixelSize: Style.font.subtitle }
      Label {
        width: parent.width
        text: root.report.demo ? strings.t("demoShort") : strings.t("sampling")
        font.pixelSize: Style.font.caption
        font.letterSpacing: 1
        color: root.report.demo ? Commons.Color.accent : root.foreground
        opacity: 0.7
      }
    }

  }
  Grid {
    width: root.width
    columns: Math.min(3, Math.max(1, root.drives.length))
    spacing: Style.space(6)
    visible: root.drives.length > 1 && !root.editing
    Repeater {
      model: root.drives
      Button {
        required property var modelData
        required property int index
        width: (root.width - (parent.columns - 1) * parent.spacing) / parent.columns
        text: (String(modelData.device).indexOf("nvme") >= 0 ? "NVMe " : "SATA ") + Commons.I18n.formatNumber(index + 1)
        fontFamily: root.fontFamily
        fontSize: Style.font.caption
        foreground: root.foreground
        bordered: true
        active: index === root.selectedIndex
        focusable: true
        onClicked: root.selectedId = modelData.id
      }
    }
  }
  Label { visible: root.report.stale !== false; text: strings.t("stale"); color: Commons.Color.urgent }
  Label { visible: root.drive.stale === true; text: strings.t("notSeen"); color: Commons.Color.urgent }
  Label { visible: !root.drives.length; text: strings.t("empty") }
  Repeater {
    model: root.report.errors || []
    Label { required property string modelData; text: modelData; color: Commons.Color.urgent }
  }

  WarrantyEditor {
    id: editor
    width: root.width
    visible: root.editing
    demo: root.report.demo === true
    onCancelled: root.editing = false
    onSaved: {
      root.selectedId = editor.driveId
      root.editing = false
      root.settingsWereSaved = true
      root.settingsSaved()
    }
  }
  Label { visible: root.settingsWereSaved && !root.editing; text: strings.t("settingsSaved"); color: Commons.Color.accent }
  Column {
    visible: root.drives.length > 0 && !root.editing
    width: root.width
    spacing: Style.space(14)
    Separator {}
    Column {
      width: root.width
      spacing: Style.space(10)
      Section { text: strings.t("endurance") }
      Label { text: root.drive.model || ""; font.pixelSize: Style.font.caption; opacity: 0.7 }
      Column {
        width: root.width
        spacing: Style.space(6)
        Pair { caption: strings.t("wearShort"); reading: root.percent(root.drive.wear_used_pct) }
        Meter { amount: root.drive.wear_used_pct }
      }
      Column {
        width: root.width
        spacing: Style.space(6)
        Pair { caption: strings.t("writesShort"); reading: root.percent(root.warranty.used_pct) }
        Meter { amount: root.warranty.used_pct }
        Label {
          text: root.warranty.rated_tbw
            ? strings.t("writeTotals").arg(root.value(root.drive.written_tb, 1)).arg(root.value(root.warranty.rated_tbw))
            : strings.t("writesUnrated").arg(root.value(root.drive.written_tb, 1))
          opacity: 0.55
          font.pixelSize: Style.font.caption
        }
      }
    }
    Separator {}
    Column {
      width: root.width
      spacing: Style.space(10)
      Item {
        width: root.width
        implicitHeight: Math.max(warrantyHeading.implicitHeight, settingsButton.implicitHeight)
        Section {
          id: warrantyHeading
          width: parent.width - settingsButton.width - Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          text: strings.t("warrantySection")
        }
        Button {
          id: settingsButton
          width: Style.space(32)
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          text: ""
          tooltipText: strings.t("settingsTitle")
          Accessible.name: strings.t("settingsTitle")
          focusable: true
          enabled: !editor.busy
          onClicked: root.openSettings()
        }
      }
      Pair { caption: strings.t("coverageEnds"); reading: root.warranty.expires ? root.date(root.warranty.expires) : strings.t("notSet") }
      Pair {
        caption: strings.t("timeRemaining")
        reading: root.warranty.days_left === null || root.warranty.days_left === undefined ? strings.t("unknown")
          : root.warranty.days_left < 0 ? strings.t("expiredShort") : strings.t("days").arg(root.value(root.warranty.days_left))
      }
      Label { visible: root.warranty.used_pct >= 100; text: strings.t("limit"); color: Commons.Color.urgent; font.pixelSize: Style.font.caption }
    }
    Separator {}
    Column {
      width: root.width
      spacing: Style.space(5)
      Section { text: strings.t("healthSection") }
      Stat { caption: strings.t("status"); reading: strings.t(root.drive.stale ? "unknown" : (root.drive.status || "unknown")); warning: ["critical", "warning"].indexOf(root.drive.status) >= 0 }
      Stat { caption: strings.t("temperatureShort"); reading: typeof root.drive.temperature_c === "number" ? root.value(root.drive.temperature_c) + " °C" : strings.t("unknown") }
      Stat { caption: strings.t("hours"); reading: root.value(root.drive.power_on_hours) }
      Stat { caption: strings.t("mediaErrors"); reading: root.value(root.drive.media_errors); warning: root.drive.media_errors > 0 }
      Repeater {
        model: root.drive.health_details || []
        Column {
          id: reasonRow
          required property var modelData
          width: root.width
          spacing: Style.space(4)
          topPadding: Style.space(8)
          Label {
            text: typeof reasonRow.modelData.value === "number"
              ? strings.t("reason.value").arg(strings.t("reason." + reasonRow.modelData.code)).arg(root.value(reasonRow.modelData.value))
              : strings.t("reason." + reasonRow.modelData.code)
            font.pixelSize: Style.font.caption
            font.bold: reasonRow.modelData.severity === "critical" || reasonRow.modelData.observation === "increased"
            color: Commons.Color.urgent
          }
          Label { text: root.observationText(reasonRow.modelData); font.pixelSize: Style.font.caption; opacity: 0.7 }
          Label {
            visible: !!reasonRow.modelData.last_increase_at
            text: strings.t("observation.lastIncrease").arg(root.observationTime(reasonRow.modelData.last_increase_at || 0))
            font.pixelSize: Style.font.caption
            opacity: 0.7
          }
        }
      }
      Label {
        visible: (root.drive.health_details || []).length > 0
        text: strings.t("observation.note")
        font.pixelSize: Style.font.caption
        opacity: 0.5
      }

    }
  }
  Label { visible: !root.editing; text: strings.t("note"); opacity: 0.45; font.pixelSize: Style.font.caption }

  component Label: Text {
    width: root.width
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    color: root.foreground
    font.family: root.fontFamily
    font.pixelSize: Style.font.body
  }
  component Section: Label {
    font.pixelSize: Style.font.caption
    font.capitalization: Font.AllUppercase
    opacity: 0.55
  }
  component Separator: Rectangle {
    width: root.width
    height: 1
    color: root.foreground
    opacity: 0.15
  }
  component Pair: Item {
    property string caption: ""
    property string reading: ""
    property bool warning: false
    width: parent.width
    implicitHeight: Math.max(leftLabel.implicitHeight, rightLabel.implicitHeight)
    Label { id: leftLabel; width: parent.width * 0.43; text: parent.caption; font.pixelSize: Style.font.caption }
    Label {
      id: rightLabel
      anchors.right: parent.right
      width: parent.width * 0.55
      horizontalAlignment: Text.AlignRight
      text: parent.reading
      font.pixelSize: Style.font.caption
      color: parent.warning ? Commons.Color.urgent : root.foreground
    }
  }
  component Stat: Rectangle {
    property string caption: ""
    property string reading: ""
    property bool warning: false
    width: root.width
    height: statPair.implicitHeight + Style.space(10)
    color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
    Pair {
      id: statPair
      anchors.centerIn: parent
      width: parent.width - Style.space(14)
      caption: parent.caption
      reading: parent.reading
      warning: parent.warning
    }
  }
  component Meter: Rectangle {
    property var amount: null
    width: root.width
    height: Style.space(4)
    radius: height / 2
    color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12)
    Rectangle {
      width: parent.width * Math.max(0, Math.min(100, parent.amount || 0)) / 100
      height: parent.height
      radius: parent.radius
      color: parent.amount >= 90 ? Commons.Color.urgent : root.foreground
      opacity: 0.65
    }
  }
}
