import sys
import time
import numpy as np
import logging

from pymeasure.experiment import Procedure, FloatParameter, IntegerParameter, ListParameter, Parameter
from pymeasure.display.windows import ManagedWindow
from pymeasure.instruments.srs import sr830
from pymeasure.instruments.newport import esp300
from PyQt5.QtWidgets import QLineEdit

# Set up logging for internal error tracking
log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class DelayScanProcedure(Procedure):
    # 1. Hardware Communication
    gpib_lockin = Parameter('Lock-In GPIB', default='GPIB::8')
    gpib_stage = Parameter('ESP300 GPIB', default='GPIB::1')
    stage_number = '2'

    # 2. Sweep Parameters
    zero_delay_pos = FloatParameter('Zero Delay Point', units='mm', default=65.66)
    start_pos = FloatParameter('Start Position', units='mm', default=66.2)
    end_pos = FloatParameter('End Position', units='mm', default=62)
    step_size = FloatParameter('Step Size', units='mm', default=0.005)
    averages = IntegerParameter('Averages per point', default=3)

    # 3. Instrument Settings
    integration_time = FloatParameter('Integration Time (TC)', units='s', default=0.1)
    sensitivity = FloatParameter('Sensitivity', units='V', default=0.000020)
    filter_slope = ListParameter('Filter Slope', choices=[6, 12, 18, 24], units='dB/oct', default=12)

    # Added Time Delay to the permanent data pipeline
    DATA_COLUMNS = ['Position (mm)', 'Time Delay (ps)', 'Voltage (V)']

    def startup(self):
        """Initializes hardware and sets PyVISA timeouts to accommodate hardware-waits."""
        log.info("Connecting to Lock-in Amplifier...")
        self.lock_in = sr830.SR830(self.gpib_lockin)
        self.lock_in.time_constant = self.integration_time
        self.lock_in.sensitivity = self.sensitivity
        self.lock_in.filter_slope = self.filter_slope

        log.info("Connecting to Stage Controller...")
        self.stages_controller = esp300.ESP300(
            self.gpib_stage,
            read_termination='\r',
            write_termination='\r',
            timeout=60000
        )
        self.stages_controller.write(f'{self.stage_number}MO')

        # Flush any stale errors from initialization
        log.info("Flushing ESP300 error buffer...")
        while True:
            err = self.stages_controller.ask('TE?')
            if int(err.split(',')[0]) == 0:
                break
            time.sleep(0.05)

    def execute(self):
        """Executes the scan using zero-traffic hardware chaining to prevent GPIB crashes."""
        c_mm_ps = 0.299792458

        # Map the selected filter slope to its required mathematical dwell multiplier
        slope_to_tau_multiplier = {
            6: 2.5,
            12: 4.0,
            18: 5.5,
            24: 7.0
        }
        tau_multiplier = slope_to_tau_multiplier[self.filter_slope]
        dwell_time = self.integration_time * tau_multiplier

        log.info(
            f"Filter slope: {self.filter_slope} dB/oct. Explicit dwell time set to {tau_multiplier}τ ({dwell_time:.3f} s).")

        # Dynamically calculate the directional vector and exact stepping array
        direction = 1 if self.end_pos >= self.start_pos else -1
        signed_step = abs(self.step_size) * direction
        epsilon = signed_step * 0.1

        theoretical_positions = np.arange(
            self.start_pos,
            self.end_pos + epsilon,
            signed_step
        )
        total_points = len(theoretical_positions)

        log.info(f"Scan initialized for {total_points} discrete points.")

        # Initial macroscopic move using hardware chaining (Move -> Wait Stop -> Tell Position)
        macro_cmd = f'2PA{theoretical_positions[0]:.4f};{self.stage_number}WS;{self.stage_number}TP?'
        log.info(f"Commanding macroscopic move: {macro_cmd}")
        actual_start = float(self.stages_controller.ask(macro_cmd))

        log.info(f"Initial move complete. True position: {actual_start} mm. Settling Lock-in...")
        time.sleep(self.integration_time * 14)

        try:
            self.lock_in.adapter.connection.clear()  # Sends IEEE-488.1 Selected Device Clear (SDC)
        except Exception:
            pass

        # Guarantee FIFO realignment by exhausting any remaining string fragments
        for _ in range(5):
            try:
                _ = self.lock_in.x
            except Exception:
                pass

        for idx, theoretical_pos in enumerate(theoretical_positions):
            if self.should_stop():
                log.warning("Measurement interrupted by user.")
                break

            # Chain the commands to offload the wait logic entirely to the ESP300 processor
            move_cmd = f'{self.stage_number}PA{theoretical_pos:.4f};{self.stage_number}WS;{self.stage_number}TP?'
            log.info(f"[{idx + 1}/{total_points}] Executing chained command: {move_cmd}")

            # The script will halt on this line, generating zero bus traffic, until the stage replies
            actual_pos_str = self.stages_controller.ask(move_cmd)
            actual_pos = float(actual_pos_str)

            # Explicit dwell time for the SR830 low-pass filter to settle
            time.sleep(self.integration_time * 4)
            self.lock_in.write("*CLS")

            # Acquire valid steady-state data
            voltage_sum = 0.0
            for _ in range(self.averages):
                voltage_sum += self.lock_in.x

            avg_voltage = voltage_sum / self.averages

            # Calculate temporal optical delay
            t_delay_ps = (2 * (self.zero_delay_pos - actual_pos)) / c_mm_ps

            # Log the true physical position and temporal delay to the CSV and GUI
            self.emit('results', {
                'Position (mm)': actual_pos,
                'Time Delay (ps)': t_delay_ps,
                'Voltage (V)': avg_voltage
            })

            self.emit('progress', 100 * (idx + 1) / total_points)

    def shutdown(self):
        """Ensures all GPIB handles are closed cleanly."""
        log.info("Scan complete. Closing connections.")
        if hasattr(self, 'lock_in'):
            self.lock_in.adapter.connection.close()
        if hasattr(self, 'stages_controller'):
            self.stages_controller.adapter.connection.close()


class ScanGUI(ManagedWindow):
    def __init__(self):
        super().__init__(
            procedure_class=DelayScanProcedure,
            inputs=[
                'gpib_lockin', 'gpib_stage',
                'zero_delay_pos', 'start_pos', 'end_pos', 'step_size',
                'averages', 'integration_time', 'sensitivity', 'filter_slope'
            ],
            displays=['zero_delay_pos', 'start_pos', 'end_pos', 'step_size'],
            x_axis='Time Delay (ps)',  # Sets the temporal axis as the default view
            y_axis='Voltage (V)'
        )
        self.setWindowTitle('Optical Probe Delay Stage & Lock-In Scanner')

        # 1. Set PyMeasure's internal directory path variable
        self.directory = r"./data/"

        # 2. Update the visual QLineEdit in the GUI (if present)
        for line_edit in self.findChildren(QLineEdit):
            # PyMeasure initializes the directory line edit with "." or the current working directory
            if line_edit.text() in ('.', '', sys.path[0]):
                line_edit.setText(self.directory)
                break


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    window = ScanGUI()
    window.show()
    sys.exit(app.exec_())