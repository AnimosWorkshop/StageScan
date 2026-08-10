import pyvisa
import time
import numpy as np


def diagnose_gpib():
    rm = pyvisa.ResourceManager()
    print("Active Resources:", rm.list_resources())

    addresses = ['GPIB0::1::INSTR', 'GPIB0::8::INSTR']

    for addr in addresses:
        print(f"\n--- Testing {addr} ---")
        try:
            # Attempt Lock-In identity query (*IDN?) with Line Feed
            inst = rm.open_resource(addr, read_termination='\n', write_termination='\n', timeout=2000)
            response = inst.query('*IDN?')
            print(f"Result: Lock-In Amplifier detected ({response.strip()})")
            inst.close()
        except pyvisa.errors.VisaIOError:
            print(f"  *IDN? failed (Timeout). Testing Newport commands...")
            try:
                # Attempt Newport ESP300 identity query (VE?) with Carriage Return
                inst = rm.open_resource(addr, read_termination='\r', write_termination='\r', timeout=2000)
                response = inst.query('VE?')
                print(f"Result: Newport ESP300 detected ({response.strip()})")

                # Test if Axis 2 can be polled independently
                axis2_pos = inst.query('2TP?')
                print(f"Axis 2 Position: {axis2_pos.strip()} mm")
                inst.close()
            except pyvisa.errors.VisaIOError as e:
                print(f"Result: Instrument unresponsive to both protocols. Error: {e}")


def test_stage_domain(rm, address, start_mm, num_points=50, step_mm=0.005):
    print(f"\n--- Initiating Test starting at {start_mm} mm ---")

    try:
        esp300 = rm.open_resource(address, read_termination='\r', write_termination='\r', timeout=5000)
        esp300.write('2MO')  # Motor ON

        # Macroscopic move to test domain
        print(f"Moving to initial position: {start_mm} mm...")
        esp300.write(f'2PA{start_mm}')

        # Wait for initial move
        while int(esp300.query('2MD?')) == 0:
            time.sleep(0.1)

        print("Initial position reached. Beginning micro-stepping...")

        positions = np.arange(start_pos, start_pos + (step_mm * num_points), step_mm)

        for idx, pos in enumerate(positions):
            safe_pos = f"{pos:.4f}"
            print(f"[{idx + 1}/{num_points}] Commanding absolute move to {safe_pos} mm...", end=" ")

            esp300.write(f'2PA{safe_pos}')

            # Polling loop with a hard 5-second timeout to detect silent freezes
            start_time = time.time()
            frozen = True

            while time.time() - start_time < 5.0:
                if int(esp300.query('2MD?')) == 1:
                    frozen = False
                    break
                time.sleep(0.1)

            if frozen:
                actual_pos = esp300.query('2TP?')
                print(f"FAIL! Stage froze. Current physical coordinate is: {actual_pos.strip()} mm.")
                esp300.close()
                return False

            print("Done.")

        print("SUCCESS! Domain passed all micro-steps without freezing.")
        esp300.close()
        return True

    except pyvisa.errors.VisaIOError as e:
        print(f"CRITICAL GPIB ERROR: {e}")
        return False


if __name__ == "__main__":
    diagnose_gpib()

    rm = pyvisa.ResourceManager()
    gpib_address = 'GPIB0::1::INSTR'

    print("Testing Domain 1 (The Optical Zero-Delay Region)")
    start_pos = 28.0
    domain_1_result = test_stage_domain(rm, gpib_address, start_pos)

    if domain_1_result:
        print("\nTesting Domain 2 (Pristine Track Region)")
        start_pos = 40.0
        test_stage_domain(rm, gpib_address, start_pos)
