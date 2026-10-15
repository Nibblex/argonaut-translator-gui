"""The file list itself: its columns, per-file data roles, the sortable row
item and the empty state the list shows before anything is added to it."""

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QLabel,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from argonaut_cli.translation import SUPPORTED_EXTS

from argonaut_gui.i18n import tr

NAME_COL = 0
TYPE_COL = 1
SIZE_COL = 2
MODIFIED_COL = 3
FOLDER_COL = 4
STATUS_COL = 5
# header text keys, one per column, in column order
COLUMN_KEYS = (
    "col_name", "col_type", "col_size", "col_modified", "col_folder", "col_status",
)
# columns the user can show/hide from the header's context menu (Name always shows)
HIDEABLE_COLS = (TYPE_COL, SIZE_COL, MODIFIED_COL, FOLDER_COL, STATUS_COL)

FILE_PATH_ROLE = Qt.UserRole
FILE_SIZE_ROLE = Qt.UserRole + 1
FILE_MTIME_ROLE = Qt.UserRole + 2
FILE_STATE_ROLE = Qt.UserRole + 3
FILE_REUSED_ROLE = Qt.UserRole + 4  # segments this file served from the cache

# per-file translation states, in the order used to sort the Status column
STATUS_STATES = ("pending", "translating", "done", "skipped", "failed", "cancelled")


def _status_rank(item):
    state = item.data(STATUS_COL, FILE_STATE_ROLE)
    return STATUS_STATES.index(state) if state in STATUS_STATES else -1


class FileItem(QTreeWidgetItem):
    """A row in the file list. The size and modified columns sort by their
    raw numeric value rather than their formatted text, and the status column
    by its state's rank; the rest sort case-insensitively by their text."""

    def __lt__(self, other):
        tree = self.treeWidget()
        column = tree.sortColumn() if tree else NAME_COL
        if column == SIZE_COL:
            return (self.data(SIZE_COL, FILE_SIZE_ROLE) or 0) < (
                other.data(SIZE_COL, FILE_SIZE_ROLE) or 0
            )
        if column == MODIFIED_COL:
            return (self.data(MODIFIED_COL, FILE_MTIME_ROLE) or 0) < (
                other.data(MODIFIED_COL, FILE_MTIME_ROLE) or 0
            )
        if column == STATUS_COL:
            return _status_rank(self) < _status_rank(other)
        return self.text(column).lower() < other.text(column).lower()


def human_size(num_bytes):
    """Human-readable byte count, e.g. 512 B, 3.4 KB, 12.0 MB."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            break
        size /= 1024
    return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"


DROP_ICON_SIZE = 48
DROP_BORDER_RADIUS = 8
# breathing room between the placeholder and the edges of the list
PLACEHOLDER_MARGIN = 4
# the wash of the selection colour laid over the viewport while a drag is
# overhead: enough to read as a target, not enough to hide the rows under it
DROP_FILL_ALPHA = 40


def drop_pixmap(style, size):
    """The folder icon at `size`.

    QIcon.pixmap() never enlarges beyond the largest variant the style ships,
    so asking a 16-pixel-only icon for 48 quietly returns 16; scaling it here
    keeps the placeholder the same size whatever the platform style offers.
    """
    pixmap = style.standardIcon(QStyle.SP_DirIcon).pixmap(size, size)
    if pixmap.width() < size:
        pixmap = pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return pixmap


class FileTree(QTreeWidget):
    """The file list, with its empty state drawn inside the list itself.

    An empty QTreeWidget is a blank box under a row of column headers, which
    says nothing about what belongs in it. The placeholder here — an icon,
    the message and the formats it takes — is a child of the viewport and
    centred on it, and the headers are hidden while there is nothing for them
    to head.

    The drag feedback is painted rather than set as a stylesheet, so it takes
    its colour from the palette and follows the light, dark and system themes
    instead of hard-coding one of them.
    """

    # emitted whenever rows appear or disappear, however they did it. The
    # window drives the buttons that act on the list from this rather than
    # from the calls that add and remove, which one of them would sooner or
    # later forget to make
    rows_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_active = False

        self.placeholder = QWidget(self.viewport())
        box = QVBoxLayout(self.placeholder)
        box.setContentsMargins(0, 0, 0, 0)  # the viewport layout below pads it
        box.setSpacing(8)

        self.drop_icon = QLabel()
        self.drop_icon.setPixmap(drop_pixmap(self.style(), DROP_ICON_SIZE))
        self.drop_icon.setAlignment(Qt.AlignCenter)

        self.hint = QLabel()
        self.hint.setAlignment(Qt.AlignCenter)

        self.formats_hint = QLabel()
        self.formats_hint.setWordWrap(True)
        self.formats_hint.setAlignment(Qt.AlignCenter)
        self.formats_hint.setStyleSheet("color: gray; font-size: 11px;")

        box.addWidget(self.drop_icon)
        box.addWidget(self.hint)
        box.addWidget(self.formats_hint)

        # a layout on the viewport keeps the placeholder centred through every
        # resize without a geometry calculation of our own. The view paints its
        # rows straight onto the viewport rather than into child widgets, so
        # the two do not compete: the placeholder is simply hidden once there
        # are rows to show
        centre = QVBoxLayout(self.viewport())
        centre.setContentsMargins(
            PLACEHOLDER_MARGIN, PLACEHOLDER_MARGIN,
            PLACEHOLDER_MARGIN, PLACEHOLDER_MARGIN,
        )
        centre.addWidget(self.placeholder, 0, Qt.AlignCenter)

        # the empty state cannot drift from the rows actually in the list:
        # every insertion, removal and clear() reports through the model
        model = self.model()
        model.rowsInserted.connect(self.refresh_empty_state)
        model.rowsRemoved.connect(self.refresh_empty_state)
        model.modelReset.connect(self.refresh_empty_state)
        self.refresh_empty_state()

    def retranslate(self):
        self.hint.setText(tr("hint"))
        self.formats_hint.setText(
            tr("hint_formats", formats=" ".join(SUPPORTED_EXTS))
        )

    def refresh_empty_state(self):
        empty = self.topLevelItemCount() == 0
        self.placeholder.setVisible(empty)
        self.setHeaderHidden(empty)
        # nothing to scroll to while the list is empty, and a scrollbar under
        # the placeholder only makes the box look like it is hiding something
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff if empty else Qt.ScrollBarAsNeeded
        )
        if empty:
            self._fit_placeholder()
        self.rows_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_placeholder()

    def _fit_placeholder(self):
        """Sheds the icon, then the formats line, until what is left of the
        placeholder fits the list.

        The list is one widget among many in the window and at the smallest
        window it is barely three rows tall. Centred in it, a placeholder that
        does not fit loses its top and bottom, which is how the message ends
        up sliced in half; showing less of it reads better than showing part
        of all of it. The message itself is never dropped.
        """
        available = self.viewport().height() - 2 * PLACEHOLDER_MARGIN
        optional = (self.drop_icon, self.formats_hint)
        for widget in optional:
            widget.setVisible(True)
        for widget in optional:
            if self.placeholder.sizeHint().height() <= available:
                break
            widget.setVisible(False)

    def set_drag_active(self, active):
        """Turns the drop highlight on and off, repainting only on a change:
        drag moves arrive continuously while the pointer is over the window."""
        if active != self._drag_active:
            self._drag_active = active
            self.viewport().update()

    def paintEvent(self, event):
        """Draws the rows as usual, then the drop target over them. In a
        QAbstractItemView this paints onto the viewport, which is what the
        highlight should cover."""
        super().paintEvent(event)
        if not self._drag_active:
            return
        viewport = self.viewport()
        colour = self.palette().highlight().color()
        fill = QColor(colour)
        fill.setAlpha(DROP_FILL_ALPHA)
        painter = QPainter(viewport)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(colour, 2, Qt.DashLine))
        painter.setBrush(fill)
        # inset by half the pen width, so the dashes land inside the viewport
        # instead of being clipped in half along its edges
        painter.drawRoundedRect(
            QRectF(viewport.rect()).adjusted(1, 1, -1, -1),
            DROP_BORDER_RADIUS,
            DROP_BORDER_RADIUS,
        )
