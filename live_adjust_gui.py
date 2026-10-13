import sys
import collections
import logging
from PyQt5 import QtWidgets, QtCore
import pyqtgraph as pg

from pymeasure.instruments.srs import sr830
from pymeasure.instruments.newport import esp300

# Set up logging for internal error tracking
log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class LiveMonitorGUI(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Live Optical Transient Monitor")
        self.resize(850, 500)

        # Default stage axis for initialization
        self.default_stage = "2"
        self.lock_in = None
        self.stages_controller = None

        # 1. Hardware Initialization
        self.init_hardware()

        # 2. Data Structures
        self.buffer_size = 100
        self.data_y = collections.deque(maxlen=self.buffer_size)

        # 3. GUI Setup
        self.init_ui()

        # 4. Asynchronous DAQ Timer
        self.daq_timer = QtCore.QTimer()
        self.daq_timer.timeout.connect(self.poll_lockin)
        self.daq_timer.start(200)

    def init_hardware(self):
        """Instantiates the hardware objects using the current front-panel configurations."""

        # Connect to Lock-in Amplifier (Non-fatal if it fails)
        try:
            log.info("Connecting to Lock-in Amplifier...")
            self.lock_in = sr830.SR830('GPIB0::8::INSTR')
        except Exception as e:
            log.warning(f"Failed to connect to SR830: {e}")
            QtWidgets.QMessageBox.warning(
                self,
                "Lock-in Warning",
                f"Failed to connect to SR830 Lock-in Amplifier.\nStage controls will still function.\n\nError details: {e}"
            )

        # Connect to Stage Controller (Fatal if it fails, as it's the core mechanical component)
        try:
            log.info("Connecting to Stage Controller...")
            self.stages_controller = esp300.ESP300(
                'GPIB0::1::INSTR',
                read_termination='\r',
                write_termination='\r',
                timeout=2000
            )
            # Ensure the default motor is powered on initially
            self.stages_controller.write(f'{self.default_stage}MO')

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Hardware Error", f"Failed to connect to Stage Controller:\n{e}")
            sys.exit(1)

    def init_ui(self):
        """Constructs the real-time plotting dashboard and stage controls."""
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QVBoxLayout(central_widget)

        # --- Plotting Area ---
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')
        self.plot_widget = pg.PlotWidget(title="Live SR830 Output")
        self.plot_widget.setLabel('left', 'Voltage', units='V')
        self.plot_widget.setLabel('bottom', 'Recent Samples (n)')
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)

        self.plot_curve = self.plot_widget.plot(pen=pg.mkPen('r', width=2))
        main_layout.addWidget(self.plot_widget)

        # --- Control Area ---
        control_layout = QtWidgets.QHBoxLayout()

        # Stage Selection
        self.stage_label = QtWidgets.QLabel("Active Stage:")
        self.stage_selector = QtWidgets.QComboBox()
        self.stage_selector.addItems(["1", "2", "3"])
        self.stage_selector.setCurrentText(self.default_stage)
        self.stage_selector.currentTextChanged.connect(self.change_active_stage)

        # Position A Controls
        self.pos_label_a = QtWidgets.QLabel("Pos A (mm):")
        self.pos_input_a = QtWidgets.QLineEdit()
        self.pos_input_a.setPlaceholderText("e.g. 28.500")
        self.goto_button_a = QtWidgets.QPushButton("Go To A")
        self.goto_button_a.clicked.connect(lambda: self.async_move_stage(self.pos_input_a.text()))

        # Position B Controls
        self.pos_label_b = QtWidgets.QLabel("Pos B (mm):")
        self.pos_input_b = QtWidgets.QLineEdit()
        self.pos_input_b.setPlaceholderText("e.g. 30.000")
        self.goto_button_b = QtWidgets.QPushButton("Go To B")
        self.goto_button_b.clicked.connect(lambda: self.async_move_stage(self.pos_input_b.text()))

        # Status bar for feedback
        self.status_label = QtWidgets.QLabel("Status: Ready.")
        if self.lock_in is None:
            self.status_label.setText("Status: Ready. (SR830 Offline)")

        self.status_label.setStyleSheet("color: green;")

        # Assemble layout
        control_layout.addWidget(self.stage_label)
        control_layout.addWidget(self.stage_selector)
        control_layout.addSpacing(15)
        control_layout.addWidget(self.pos_label_a)
        control_layout.addWidget(self.pos_input_a)
        control_layout.addWidget(self.goto_button_a)
        control_layout.addSpacing(15)
        control_layout.addWidget(self.pos_label_b)
        control_layout.addWidget(self.pos_input_b)
        control_layout.addWidget(self.goto_button_b)
        control_layout.addStretch()
        control_layout.addWidget(self.status_label)

        main_layout.addLayout(control_layout)

    def change_active_stage(self, stage_str):
        """Wakes up the selected stage's motor when the user changes the dropdown."""
        try:
            self.stages_controller.write(f'{stage_str}MO')
            self.status_label.setText(f"Status: Stage {stage_str} activated.")
            self.status_label.setStyleSheet("color: green;")
        except Exception as e:
            self.status_label.setText(f"Status: Hardware Error - {e}")
            self.status_label.setStyleSheet("color: red;")

    def poll_lockin(self):
        """Fired automatically by the QTimer. Queries the SR830 and updates the plot."""
        if self.lock_in is None:
            return  # Skip polling if the lock-in failed to connect

        try:
            voltage = self.lock_in.x
            self.data_y.append(voltage)
            self.plot_curve.setData(list(self.data_y))
        except Exception as e:
            log.warning(f"Dropped frame: {e}")

    def async_move_stage(self, target_str):
        """
        Executes a 'fire-and-forget' command. The ESP300 DSP takes over the
        kinematics, immediately returning control of the Python thread.
        """
        try:
            target_pos = float(target_str)
            safe_pos = f"{target_pos:.4f}"
            current_stage = self.stage_selector.currentText()

            # Send Absolute Move command to the currently selected stage
            self.stages_controller.write(f'{current_stage}PA{safe_pos}')

            self.status_label.setText(f"Status: Stage {current_stage} commanded to {safe_pos} mm")
            self.status_label.setStyleSheet("color: blue;")

        except ValueError:
            self.status_label.setText("Status: Invalid numerical input.")
            self.status_label.setStyleSheet("color: red;")
        except Exception as e:
            self.status_label.setText(f"Status: Hardware Error - {e}")
            self.status_label.setStyleSheet("color: red;")

    def closeEvent(self, event):
        """Ensures the hardware connections are gracefully terminated upon exit."""
        self.daq_timer.stop()
        if self.lock_in is not None and hasattr(self.lock_in, 'adapter'):
            self.lock_in.adapter.connection.close()
        if self.stages_controller is not None and hasattr(self.stages_controller, 'adapter'):
            self.stages_controller.adapter.connection.close()
        event.accept()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv