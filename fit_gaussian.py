import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit


def gaussian_with_offset(t, a, A, t0, fwhm_ac):
    """
    Gaussian function with constant offset:
    y(t) = a + A * exp(-4 * ln(2) * ((t - t0) / fwhm_ac)^2)

    Parameters:
    - a: Baseline / offset voltage
    - A: Peak amplitude above baseline
    - t0: Center delay (ps)
    - fwhm_ac: Autocorrelation FWHM (ps)
    """
    return a + A * np.exp(-4 * np.log(2) * ((t - t0) / fwhm_ac) ** 2)


def analyze_autocorrelation(file_path, plot=True):
    # 1. Read CSV while skipping metadata comment lines starting with '#'
    df = pd.read_csv(file_path, comment='#')

    # Strip whitespace from column names if present
    df.columns = df.columns.str.strip()

    # Time delay is typically in ps; convert to fs for standard pulse analysis
    time_ps = df['Time Delay (ps)'].to_numpy()
    time_fs = time_ps * 1e3
    voltage_v = df['Voltage (V)'].to_numpy()

    # 2. Initial parameter guesses for curve fitting
    offset_guess = np.min(voltage_v)
    amp_guess = np.max(voltage_v) - offset_guess
    t0_guess = time_fs[np.argmax(voltage_v)]

    # Estimate FWHM by finding points above half max
    half_max = offset_guess + amp_guess / 2.0
    above_half = time_fs[voltage_v >= half_max]
    fwhm_guess = np.ptp(above_half) if len(above_half) > 1 else np.ptp(time_fs) / 4.0

    p0 = [offset_guess, amp_guess, t0_guess, fwhm_guess]

    # 3. Non-linear Least Squares Fit
    popt, pcov = curve_fit(gaussian_with_offset, time_fs, voltage_v, p0=p0)
    perr = np.sqrt(np.diag(pcov))

    a_fit, A_fit, t0_fit, tau_ac = popt
    tau_ac_err = perr[3]

    # 4. Deconvolution: for a Gaussian pulse, tau_pulse = tau_ac / sqrt(2)
    tau_pulse = np.abs(tau_ac) / np.sqrt(2)
    tau_pulse_err = tau_ac_err / np.sqrt(2)

    print("=" * 45)
    print("AUTOCORRELATION FIT RESULTS")
    print("=" * 45)
    print(f"Offset (a):            {a_fit:.6e} V (± {perr[0]:.2e})")
    print(f"Amplitude (A):         {A_fit:.6e} V (± {perr[1]:.2e})")
    print(f"Center Time (t0):      {t0_fit:.2f} fs (± {perr[2]:.2f})")
    print(f"Autocorrelation FWHM:  {abs(tau_ac):.2f} fs (± {tau_ac_err:.2f})")
    print(f"Original Pulse FWHM:   {tau_pulse:.2f} fs (± {tau_pulse_err:.2f})")
    print("=" * 45)

    # 5. Visualization
    if plot:
        t_dense = np.linspace(np.min(time_fs), np.max(time_fs), 1000)
        v_fit = gaussian_with_offset(t_dense, *popt)

        plt.figure(figsize=(8, 5))
        plt.plot(time_fs, voltage_v, 'o', markersize=3, label='Experimental Data', alpha=0.7)
        plt.plot(t_dense, v_fit, 'r-', lw=2, label=f'Fit (FWHM = {abs(tau_ac):.1f} fs)')

        plt.xlabel('Time Delay (fs)')
        plt.ylabel('Voltage (V)')
        plt.title(f'SHG Autocorrelation ($\\tau_p = {tau_pulse:.1f}$ fs)')
        plt.legend(frameon=True)
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()

    return {
        'tau_ac_fs': abs(tau_ac),
        'tau_ac_err_fs': tau_ac_err,
        'tau_pulse_fs': tau_pulse,
        'tau_pulse_err_fs': tau_pulse_err,
        'fit_params': popt,
        'cov_matrix': pcov
    }

results = analyze_autocorrelation("./data/DATA_?.csv")
print(results)