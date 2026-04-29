import sys, os, platform, psutil, shutil
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QTreeWidget, QTreeWidgetItem, QTabWidget,
                             QTextEdit, QLineEdit, QPushButton, QLabel, QFrame,
                             QSplitter, QStatusBar, QGroupBox, QGridLayout)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QIcon, QColor, QPalette, QPainter, QBrush, QPen, QPixmap

AGENT_VERSION = "v3.5"
PLATFORM = platform.system()
DEVICE_NAME = "givi"
TOTAL_RAM = round(psutil.virtual_memory().total / (1024**3), 1)
CPU_NAME = platform.processor() or "Intel i7-13700H"
DISK_TOTAL = round(psutil.disk_usage('C:').total / (1024**3), 0)

BLUE_MAIN = "#0a1628"
BLUE_DARK = "#060f1e"
BLUE_ACCENT = "#0088ff"
BLUE_LIGHT = "#00c8ff"
BLUE_CARD = "#0d1f3d"
BLUE_TEXT = "#90b8e8"
WHITE = "#e8f0ff"
GREEN_ON = "#00ff88"
GRAY_LINE = "#1a3a5a"

STYLE_GLOBAL = f"""
QMainWindow, QWidget {{ background-color: {BLUE_MAIN}; color: {WHITE}; font-family: 'Segoe UI', sans-serif; }}
QTreeWidget {{ background-color: {BLUE_CARD}; border: 1px solid {GRAY_LINE}; border-radius: 8px; padding: 4px; color: {WHITE}; font-size: 13px; }}
QTreeWidget::item {{ padding: 6px; border-bottom: 1px solid {GRAY_LINE}; }}
QTreeWidget::item:selected {{ background: {BLUE_ACCENT}; color: white; }}
QTabWidget::pane {{ background: {BLUE_CARD}; border: 1px solid {GRAY_LINE}; border-radius: 8px; }}
QTabBar::tab {{ background: {BLUE_DARK}; color: {BLUE_TEXT}; padding: 8px 20px; border: 1px solid {GRAY_LINE}; border-bottom: none; border-radius: 6px 6px 0 0; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {BLUE_CARD}; color: {WHITE}; border-color: {BLUE_ACCENT}; }}
QTextEdit {{ background: {BLUE_DARK}; color: {BLUE_LIGHT}; border: 1px solid {GRAY_LINE}; border-radius: 6px; font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 13px; padding: 8px; }}
QLineEdit {{ background: {BLUE_DARK}; color: {WHITE}; border: 1px solid {GRAY_LINE}; border-radius: 6px; padding: 8px 12px; font-size: 13px; }}
QLineEdit:focus {{ border-color: {BLUE_ACCENT}; }}
QPushButton {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #0066cc, stop:1 #004488); color: white; border: none; border-radius: 6px; padding: 8px 18px; font-size: 13px; font-weight: bold; }}
QPushButton:hover {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #0088ff, stop:1 #0066cc); }}
QPushButton:pressed {{ background: {BLUE_ACCENT}; }}
QGroupBox {{ border: 1px solid {GRAY_LINE}; border-radius: 8px; margin-top: 12px; padding: 12px; font-weight: bold; color: {BLUE_LIGHT}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
QLabel {{ color: {BLUE_TEXT}; font-size: 13px; }}
QStatusBar {{ background: {BLUE_DARK}; border-top: 1px solid {GRAY_LINE}; color: {BLUE_TEXT}; font-size: 12px; }}
QScrollBar:vertical {{ background: {BLUE_DARK}; width: 8px; border-radius: 4px; }}
QScrollBar::handle:vertical {{ background: {BLUE_ACCENT}; border-radius: 4px; min-height: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""

class StatusIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(1000)
        self.dot_on = True

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(p.Antialiasing)
        color = QColor(GREEN_ON) if self.dot_on else QColor(BLUE_ACCENT)
        p.setBrush(QBrush(color))
        p.setPen(QPen(QColor("#003322" if self.dot_on else "#002244"), 1))
        p.drawEllipse(1, 1, 10, 10)
        self.dot_on = not self.dot_on

class AgentUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"ИРУ Агент {AGENT_VERSION} — {DEVICE_NAME}")
        self.setGeometry(200, 100, 1100, 700)
        ico_path = os.path.join(os.path.dirname(__file__), "IruIcon.ico")
        if os.path.exists(ico_path):
            self.setWindowIcon(QIcon(ico_path))
        self.setStyleSheet(STYLE_GLOBAL)
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 6)
        main_layout.setSpacing(8)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        # Левая панель — дерево устройств
        left_wrap = QWidget()
        left_layout = QVBoxLayout(left_wrap)
        left_layout.setContentsMargins(0, 0, 0, 0)
        title_label = QLabel("УСТРОЙСТВА")
        title_label.setStyleSheet(f"color: {BLUE_LIGHT}; font-size: 14px; font-weight: bold; padding: 4px 0;")
        left_layout.addWidget(title_label)
        self.device_tree = QTreeWidget()
        self.device_tree.setHeaderHidden(True)
        self.device_tree.setIndentation(16)
        dev = QTreeWidgetItem([f"  {DEVICE_NAME}"])
        dev.setForeground(0, QColor(GREEN_ON))
        dev.addChild(QTreeWidgetItem([f"  CPU: {CPU_NAME[:35]}"]))
        ram_str = f"  RAM: {TOTAL_RAM} ГБ | Свободно: {psutil.virtual_memory().available / (1024**3):.1f} ГБ"
        dev.addChild(QTreeWidgetItem([ram_str]))
        disk_str = f"  Диск C: {DISK_TOTAL} ГБ | Свободно: {psutil.disk_usage('C:').free / (1024**3):.1f} ГБ"
        dev.addChild(QTreeWidgetItem([disk_str]))
        dev.addChild(QTreeWidgetItem([f"  ОС: {PLATFORM}"]))
        dev.addChild(QTreeWidgetItem([f"  Агент: {AGENT_VERSION}"]))
        dev.setExpanded(True)
        self.device_tree.addTopLevelItem(dev)
        left_layout.addWidget(self.device_tree)
        # Инфо-блок
        info_box = QGroupBox("ИНФОРМАЦИЯ")
        info_layout = QVBoxLayout(info_box)
        info_layout.setSpacing(4)
        for txt in ["Сервер: ИРУ Cloud", f"Устройство: {DEVICE_NAME}", "Статус: Подключён"]:
            lbl = QLabel(txt)
            lbl.setStyleSheet(f"color: {BLUE_TEXT}; font-size: 12px;")
            info_layout.addWidget(lbl)
        left_layout.addWidget(info_box)
        left_layout.addStretch()
        splitter.addWidget(left_wrap)
        # Центральная часть — вкладки
        tabs = QTabWidget()
        # Вкладка Терминал
        term_tab = QWidget()
        term_layout = QVBoxLayout(term_tab)
        term_layout.setContentsMargins(8, 8, 8, 8)
        self.output_area = QTextEdit()
        self.output_area.setReadOnly(True)
        self.output_area.setPlaceholderText("Ожидание команд...")
        term_layout.addWidget(self.output_area)
        input_row = QHBoxLayout()
        self.input_line = QLineEdit()
        self.input_line.setPlaceholderText("Введите команду...")
        self.input_line.returnPressed.connect(self.send_command)
        self.send_btn = QPushButton("Отправить")
        self.send_btn.clicked.connect(self.send_command)
        self.clear_btn = QPushButton("Очистить")
        self.clear_btn.setStyleSheet(self.clear_btn.styleSheet().replace("QPushButton", "QPushButton.clean"))
        self.clear_btn.clicked.connect(self.output_area.clear)
        input_row.addWidget(self.input_line)
        input_row.addWidget(self.send_btn)
        input_row.addWidget(self.clear_btn)
        term_layout.addLayout(input_row)
        tabs.addTab(term_tab, "Терминал")
        # Вкладка Панель управления
        ctrl_tab = QWidget()
        ctrl_layout = QVBoxLayout(ctrl_tab)
        ctrl_layout.setContentsMargins(8, 8, 8, 8)
        sys_group = QGroupBox("СИСТЕМА")
        sys_grid = QGridLayout(sys_group)
        sys_grid.setSpacing(8)
        items = [
            ("Процессор", CPU_NAME),
            ("ОЗУ", f"{TOTAL_RAM} ГБ"),
            ("ОС", f"{PLATFORM} ({platform.release()})"),
            ("Диск C:", f"{DISK_TOTAL:.0f} ГБ"),
            ("Загрузка CPU", f"{psutil.cpu_percent(interval=0.1)}%"),
            ("Исп. RAM", f"{psutil.virtual_memory().percent}%"),
        ]
        for i, (k, v) in enumerate(items):
            row, col = divmod(i, 2)
            kl = QLabel(k + ":")
            kl.setStyleSheet(f"color: {BLUE_ACCENT}; font-weight: bold;")
            vl = QLabel(v)
            sys_grid.addWidget(kl, row, col*2)
            sys_grid.addWidget(vl, row, col*2+1)
        ctrl_layout.addWidget(sys_group)
        tasks_group = QGroupBox("АКТИВНЫЕ ЗАДАЧИ")
        tasks_layout = QVBoxLayout(tasks_group)
        tasks_layout.addWidget(QLabel("Нет активных задач"))
        ctrl_layout.addWidget(tasks_group)
        ctrl_layout.addStretch()
        tabs.addTab(ctrl_tab, "Панель управления")
        splitter.addWidget(tabs)
        splitter.setSizes([280, 820])
        main_layout.addWidget(splitter)
        # Статусбар
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_indicator = StatusIndicator()
        self.status_bar.addPermanentWidget(self.status_indicator)
        status_label = QLabel("Подключено к ИРУ Cloud")
        status_label.setStyleSheet(f"color: {GREEN_ON}; font-size: 12px;")
        self.status_bar.addPermanentWidget(status_label)
        ver_label = QLabel(f"  {AGENT_VERSION}")
        ver_label.setStyleSheet(f"color: {BLUE_TEXT}; font-size: 12px;")
        self.status_bar.addPermanentWidget(ver_label)
        self.status_bar.showMessage("Готов к работе")

    def send_command(self):
        cmd = self.input_line.text().strip()
        if cmd:
            self.output_area.append(f"[ИРУ] >> {cmd}")
            self.input_line.clear()
            self.output_area.append(f"[{DEVICE_NAME}] Команда получена. Выполнение...\n")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = AgentUI()
    w.show()
    sys.exit(app.exec_())
