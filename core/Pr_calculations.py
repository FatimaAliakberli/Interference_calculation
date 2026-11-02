#!/usr/bin/env python3
"""
Rec. ITU-R S.1430, Annex 1, Section 2.3 - determination of the threshold
interference level Pr(p) of an interfering emission (Eq. 3).

    Pr(p) = 10*log10(k * Te * B) + NL + 10*log10(10**(Ms/10) - 1) - W   [dBW]

where:
  k   - Boltzmann's constant (J/K)                          [fixed constant]
  Te  - thermal noise temperature of the receiving system (K)
  B   - reference bandwidth (Hz)
  NL  - link noise contribution (dB)
  Ms  - link performance margin (dB)
  W   - equivalence factor (dB)

Note 1 (Te) and the "practical values of Ms" table both give recommended
defaults as a function of frequency, to be used "since the location and
precise characteristics of the station are unknown". This module exposes
those as lookups the caller can use to pre-fill fields, but every value
remains user-editable before the final Pr(p) calculation.
"""

import argparse
import math

K_BOLTZMANN = 1.38e-23  # J/K
NL_DEFAULT = 1.0        # dB, "assumed NL = 1 dB for fixed-satellite links" absent other data


def lookup_te(freq_ghz):
    """Note 1: recommended receiving system noise temperature Te (K) by
    frequency band, used when the receiving station's actual characteristics
    are unknown."""
    if freq_ghz < 10:
        return 75.0
    elif freq_ghz <= 17:
        return 150.0
    else:
        return 300.0


def lookup_ms_default(freq_ghz):
    """Practical values of Ms (dB) by frequency band (Note 3 table)."""
    if freq_ghz < 10:
        return 2.0
    elif freq_ghz <= 17:
        return 4.0
    else:
        return 6.0


def pr_p(Te, B, NL, Ms, W):
    """Eq. 3: threshold interference level Pr(p), in dBW."""
    if Te <= 0:
        raise ValueError("Te must be > 0 K")
    if B <= 0:
        raise ValueError("B must be > 0 Hz")
    if Ms <= 0:
        raise ValueError("Ms must be > 0 dB (10^(Ms/10) - 1 would be <= 0, undefined log)")

    thermal_noise_dBW = 10.0 * math.log10(K_BOLTZMANN * Te * B)
    margin_term_dB = 10.0 * math.log10(10.0 ** (Ms / 10.0) - 1.0)
    pr = thermal_noise_dBW + NL + margin_term_dB - W

    return {
        "k": K_BOLTZMANN,
        "thermal_noise_dBW": thermal_noise_dBW,
        "margin_term_dB": margin_term_dB,
        "Pr_p": pr,
    }


def main():
    parser = argparse.ArgumentParser(description="Rec. ITU-R S.1430 Eq. 3 - Pr(p) calculator")
    sub = parser.add_subparsers(dest="mode", required=True)

    lk = sub.add_parser("lookup", help="Recommended default Te and Ms for a given frequency")
    lk.add_argument("--freq_ghz", type=float, required=True, help="Operating frequency (GHz)")

    pr = sub.add_parser("pr", help="Eq. 3: compute Pr(p) from Te, B, NL, Ms, W")
    pr.add_argument("--Te", type=float, required=True, help="Receiving system noise temperature (K)")
    pr.add_argument("--B", type=float, required=True, help="Reference bandwidth (Hz)")
    pr.add_argument("--NL", type=float, default=NL_DEFAULT, help="Link noise contribution (dB)")
    pr.add_argument("--Ms", type=float, required=True, help="Link performance margin (dB)")
    pr.add_argument("--W", type=float, required=True, help="Equivalence factor (dB)")

    args = parser.parse_args()

    try:
        if args.mode == "lookup":
            results = {
                "Te": lookup_te(args.freq_ghz),
                "Ms_default": lookup_ms_default(args.freq_ghz),
            }
        elif args.mode == "pr":
            results = pr_p(args.Te, args.B, args.NL, args.Ms, args.W)
        else:
            raise ValueError(f"unknown mode: {args.mode}")

        for k, v in results.items():
            if isinstance(v, float):
                # k (Boltzmann's constant) needs more precision than a fixed 6dp would give
                if k == "k":
                    print(f"{k}={v:.6e}")
                else:
                    print(f"{k}={v:.6f}")
            else:
                print(f"{k}={v}")
        print("status=OK")
    except Exception as e:
        print(f"error={e}")
        print("status=ERROR")


if __name__ == "__main__":
    main()