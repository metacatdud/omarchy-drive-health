import QtQuick
import QtQuick.Controls
import qs.Ui
import qs.Commons
import qs.Commons as Commons

Panel {
  id: root
  moduleName: "community.drive-health"
  ipcTarget: "community.drive-health"
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  onOpenedChanged: if (opened) healthSource.refresh()
  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }
  ReportSource { id: healthSource; demo: root.setting("demo", false) === true }
  readonly property bool attention: healthSource.report.stale !== false || (healthSource.report.errors || []).length > 0 || (healthSource.report.drives || []).some(function(d) { return d.status !== "ok" })

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰋊"
    active: root.attention
    // Static catalog text only enters the shell's tooltip component.
    tooltipText: strings.t("title")
    onPressed: root.toggle()
  }
  KeyboardPanel {
    id: popup
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: popup.loadedContent
    contentWidth: popup.fittedContentWidth(Style.space(350))
    contentHeight: popup.fittedContentHeight(popup.loadedContentHeight, Style.space(640))
    content: PanelKeyCatcher {
      blocked: details.editing
      implicitHeight: details.implicitHeight
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) {
        if (dx !== 0) { details.cycleDrive(dx); return }
        scroll.contentItem.contentY = Math.max(0, Math.min(details.height - scroll.height, scroll.contentItem.contentY + dy * Style.space(60)))
      }
      onTextKey: function(text, modifiers) { if (text.toLowerCase() === "r") healthSource.refresh() }
      ScrollView {
        id: scroll
        anchors.fill: parent
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        contentWidth: availableWidth
        DriveList {
          id: details
          width: scroll.availableWidth
          report: healthSource.report
          onSettingsSaved: healthSource.refresh()
          onEditingChanged: {
            scroll.contentItem.contentY = 0
            if (!editing) popup.loadedContent.forceActiveFocus()
          }
        }
      }
    }
  }
}
