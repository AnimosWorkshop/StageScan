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
        self.resize(800, 500)

        # 1. Hardware Initialization
        self.init_hardware()

        # 2. Data Structures
        # A deque acts as a C-optimized ring buffer.
        # When point 31 is added, point 1 is automatically discarded.
        self.buffer_size = 100
        self.data_y = collections.deque(maxlen=self.buffer_size)

        # 3. GUI Setup
        self.init_ui()

        # 4. Asynchronous DAQ Timer
        self.daq_timer = QtCore.QTimer()
        self.daq_timer.timeout.connect(self.poll_lockin)
        # Poll the Lock-in at 5 Hz (every 200 ms).
        # Adjust this rate based on your chosen Time Constant (tau).
        self.daq_timer.start(200)

    def init_hardware(self):
        """Instantiates the hardware objects using the current front-panel configurations."""
        try:
            log.info("Connecting to Lock-in Amplifier...")
            self.lock_in = sr830.SR830('GPIB0::8::INSTR')

            log.info("Connecting to Stage Controller...")
            self.stages_controller = esp300.ESP300(
                'GPIB0::1::INSTR',
                read_termination='\r',
                write_termination='\r',
                timeout=2000  # Keep timeout low so the live loop doesn't hang on read errors
            )
            # Ensure the motor is powered on
            self.stages_controller.write('2MO')

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Hardware Error", f"Failed to connect to hardware:\n{e}")
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

        # Initialize an empty red curve
        self.plot_curve = self.plot_widget.plot(pen=pg.mkPen('r', width=2))
        main_layout.addWidget(self.plot_widget)

        # --- Control Area ---
        control_layout = QtWidgets.QHBoxLayout()

        self.pos_label = QtWidgets.QLabel("Target Position (mm):")
        self.pos_input = QtWidgets.QLineEdit()
        self.pos_input.setPlaceholderText("e.g. 28.500")

        self.goto_button = QtWidgets.QPushButton("Go To Position")
        self.goto_button.clicked.connect(self.async_move_stage)

        # Status bar for feedback
        self.status_label = QtWidgets.QLabel("Status: Monitoring...")
        self.status_label.setStyleSheet("color: green;")

        control_layout.addWidget(self.pos_label)
        control_layout.addWidget(self.pos_input)
        control_layout.addWidget(self.goto_button)
        control_layout.addStretch()
        control_layout.addWidget(self.status_label)

        main_layout.addLayout(control_layout)

    def poll_lockin(self):
        """Fired automatically by the QTimer. Queries the SR830 and updates the plot."""
        try:
            # Query the in-phase voltage (X)
            voltage = self.lock_in.x
            self.data_y.append(voltage)

            # Update the pyqtgraph curve
            # We map the deque to a list for plotting. The X-axis defaults to the index [0 to 29].
            self.plot_curve.setData(list(self.data_y))

        except Exception as e:
            # If the GPIB bus collides briefly, catch it so the GUI doesn't crash
            log.warning(f"Dropped frame: {e}")

    def async_move_stage(self):
        """
        Executes a 'fire-and-forget' command. The ESP300 DSP takes over the
        kinematics, immediately returning control of the Python thread.
        """
        try:
            target_str = self.pos_input.text()
            target_pos = float(target_str)

            # Format to strict hardware encoder resolution to prevent string overflow
            safe_pos = f"{target_pos:.4f}"

            # Send Absolute Move command. Notice the absence of '2WS' (Wait for Stop).
            self.stages_controller.write(f'2PA{safe_pos}')

            self.status_label.setText(f"Status: Stage commanded to {safe_pos} mm")
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
        if hasattr(self, 'lock_in'):
            self.lock_in.adapter.connection.close()
        if hasattr(self, 'stages_controller'):
            self.stages_controller.adapter.connection.close()
        event.accept()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = LiveMonitorGUI()
    window.show()
    sys.exit(app.exec_())