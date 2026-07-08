# Vibration Analysis Report

## Input

- Path: `data\runs\run_20260707_6000rpm_9min\acceleration_25600Hz_256000samples\000040_segment_acceleration_25600Hz_256000samples_start9984000.npz.xz`
- Sample rate: `25600.0` Hz
- Unit: `g`
- Channels: `cDAQ9185-254D6AAMod1/ai0, cDAQ9185-254D6AAMod1/ai1, cDAQ9185-254D6AAMod1/ai2`

- RPM: `6000.0`
- Rotating frequency: `100.0` Hz

## Channel `cDAQ9185-254D6AAMod1/ai0`

| feature               |         value |
|:----------------------|--------------:|
| mean                  |    0.00191993 |
| std                   |    0.0831994  |
| rms                   |    0.0832215  |
| min                   |   -0.445541   |
| max                   |    0.495381   |
| peak_abs              |    0.495381   |
| peak_to_peak          |    0.940922   |
| absolute_mean         |    0.0658395  |
| energy                | 1773.01       |
| crest_factor          |    5.95256    |
| impulse_factor        |    7.52407    |
| shape_factor          |    1.264      |
| clearance_factor      |    8.91533    |
| skewness              |    0.0311587  |
| kurtosis              |    3.23435    |
| zero_crossing_rate_hz | 9318.64       |

### Spectral Peaks

|   frequency_hz |   amplitude |   order |
|---------------:|------------:|--------:|
|         5229   |   0.0402732 |  52.29  |
|         4203.2 |   0.0394908 |  42.032 |
|         6054.8 |   0.0310217 |  60.548 |
|         5129   |   0.0292816 |  51.29  |
|         3177.4 |   0.0206049 |  31.774 |
|         2977.4 |   0.0200186 |  29.774 |
|         6254.8 |   0.0189292 |  62.548 |
|         4400   |   0.0185404 |  44     |
|         5127.5 |   0.0176021 |  51.275 |
|          774.2 |   0.017238  |   7.742 |

### Envelope Spectral Peaks

|   frequency_hz |   amplitude |   order |
|---------------:|------------:|--------:|
|         1025.8 |  0.0260968  |  10.258 |
|          100   |  0.0163445  |   1     |
|          925.8 |  0.0142911  |   9.258 |
|          825.8 |  0.013597   |   8.258 |
|         1851.6 |  0.0122195  |  18.516 |
|         1125.8 |  0.0101625  |  11.258 |
|         2051.6 |  0.01008    |  20.516 |
|         1951.6 |  0.00937702 |  19.516 |
|         2151.6 |  0.00845211 |  21.516 |
|         2251.6 |  0.00840596 |  22.516 |

### Analysis Notes

- High crest factor suggests impulsive content.

### Figures

- [Waveform](figures/0_cDAQ9185-254D6AAMod1_ai0_waveform.png)
- [Amplitude spectrum](figures/0_cDAQ9185-254D6AAMod1_ai0_spectrum.png)
- [Welch PSD](figures/0_cDAQ9185-254D6AAMod1_ai0_psd.png)
- [Envelope spectrum](figures/0_cDAQ9185-254D6AAMod1_ai0_envelope_spectrum.png)

## Channel `cDAQ9185-254D6AAMod1/ai1`

| feature               |         value |
|:----------------------|--------------:|
| mean                  |    0.00343401 |
| std                   |    0.0784713  |
| rms                   |    0.0785464  |
| min                   |   -0.387245   |
| max                   |    0.341661   |
| peak_abs              |    0.387245   |
| peak_to_peak          |    0.728907   |
| absolute_mean         |    0.0638758  |
| energy                | 1579.4        |
| crest_factor          |    4.93015    |
| impulse_factor        |    6.06247    |
| shape_factor          |    1.22967    |
| clearance_factor      |    7.07413    |
| skewness              |    0.0165035  |
| kurtosis              |    2.66801    |
| zero_crossing_rate_hz | 9682.24       |

### Spectral Peaks

|   frequency_hz |   amplitude |   order |
|---------------:|------------:|--------:|
|         5229   |   0.0719837 |  52.29  |
|         4203.2 |   0.0232987 |  42.032 |
|         3801.5 |   0.0206551 |  38.015 |
|         4102   |   0.0196535 |  41.02  |
|         3701.5 |   0.0189041 |  37.015 |
|         5227.5 |   0.0167127 |  52.275 |
|         4103.2 |   0.0160573 |  41.032 |
|         6054.8 |   0.0136437 |  60.548 |
|         5027.5 |   0.0114351 |  50.275 |
|         5129   |   0.0102546 |  51.29  |

### Envelope Spectral Peaks

|   frequency_hz |   amplitude |   order |
|---------------:|------------:|--------:|
|         1025.8 |  0.0172198  |  10.258 |
|         1127   |  0.0124606  |  11.27  |
|         1125.8 |  0.0122955  |  11.258 |
|         1427.5 |  0.0120923  |  14.275 |
|         1527.5 |  0.0118727  |  15.275 |
|            1.5 |  0.0113761  |   0.015 |
|          825.8 |  0.0104021  |   8.258 |
|          201.5 |  0.00878674 |   2.015 |
|          100   |  0.00770319 |   1     |
|          925.8 |  0.0068389  |   9.258 |

### Analysis Notes

- The spectrum contains a dominant peak.

### Figures

- [Waveform](figures/1_cDAQ9185-254D6AAMod1_ai1_waveform.png)
- [Amplitude spectrum](figures/1_cDAQ9185-254D6AAMod1_ai1_spectrum.png)
- [Welch PSD](figures/1_cDAQ9185-254D6AAMod1_ai1_psd.png)
- [Envelope spectrum](figures/1_cDAQ9185-254D6AAMod1_ai1_envelope_spectrum.png)

## Channel `cDAQ9185-254D6AAMod1/ai2`

| feature               |          value |
|:----------------------|---------------:|
| mean                  |     0.00199892 |
| std                   |     0.0624094  |
| rms                   |     0.0624414  |
| min                   |    -0.237159   |
| max                   |     0.240695   |
| peak_abs              |     0.240695   |
| peak_to_peak          |     0.477854   |
| absolute_mean         |     0.0506011  |
| energy                |   998.125      |
| crest_factor          |     3.85473    |
| impulse_factor        |     4.75671    |
| shape_factor          |     1.23399    |
| clearance_factor      |     5.56846    |
| skewness              |     0.0672083  |
| kurtosis              |     2.67832    |
| zero_crossing_rate_hz | 13406.5        |

### Spectral Peaks

|   frequency_hz |   amplitude |   order |
|---------------:|------------:|--------:|
|         7080.6 |  0.0502698  |  70.806 |
|         5229   |  0.0362177  |  52.29  |
|         7078.5 |  0.0203917  |  70.785 |
|         7280.6 |  0.0185702  |  72.806 |
|         6154.8 |  0.0144338  |  61.548 |
|         9232.2 |  0.010085   |  92.322 |
|         6153   |  0.00899185 |  61.53  |
|        11280.5 |  0.00878441 | 112.805 |
|         7380.6 |  0.00852058 |  73.806 |
|         1125.5 |  0.00831247 |  11.255 |

### Envelope Spectral Peaks

|   frequency_hz |   amplitude |   order |
|---------------:|------------:|--------:|
|         1851.6 |  0.0214064  |  18.516 |
|          200   |  0.0136702  |   2     |
|          925.8 |  0.0130297  |   9.258 |
|            2.1 |  0.011338   |   0.021 |
|         1849.5 |  0.00777817 |  18.495 |
|         2051.6 |  0.00775337 |  20.516 |
|          300   |  0.00630461 |   3     |
|         1125.8 |  0.0059047  |  11.258 |
|         4003.2 |  0.00580686 |  40.032 |
|         4199.9 |  0.00509185 |  41.999 |

### Analysis Notes

- Energy is concentrated in 5000.0-12800.0 Hz with ratio 0.949.

### Figures

- [Waveform](figures/2_cDAQ9185-254D6AAMod1_ai2_waveform.png)
- [Amplitude spectrum](figures/2_cDAQ9185-254D6AAMod1_ai2_spectrum.png)
- [Welch PSD](figures/2_cDAQ9185-254D6AAMod1_ai2_psd.png)
- [Envelope spectrum](figures/2_cDAQ9185-254D6AAMod1_ai2_envelope_spectrum.png)
