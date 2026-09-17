# Hardware

## Bill of materials

Assuming the Pi is the only thing you buy, this comes to roughly $50–75.

| Qty | Part | Why |
|---|---|---|
| 1 | **Raspberry Pi Zero 2 W**, headers pre-soldered | −20 to +70 °C, ~0.6 W, micro-USB OTG. The board this build wants. |
| 1 | microSD 32 GB, high-endurance | Root + two 8 GB sparse images. Dashcam-rated: every ignition-off is an unclean cut. |
| 1 | USB-A to micro-USB **data** cable, ~30 cm | Power and data on one cable. Charge-only cables are common and fail silently. |
| 1 | Active piezo buzzer, 3–5 V | Status. Buy **active**, not passive — it makes its own tone from a DC level. |
| 1 | NPN transistor (2N2222, PN2222A, BC337…) | Buzzers draw ~30 mA; a GPIO should source no more than 16. |
| 1 | 1 kΩ resistor | Base series. |
| 1 | 10 kΩ resistor | Base to GND, so a floating pin cannot sound the buzzer. |
| 1 | Inline USB power meter | Measures what the car port actually supplies. Buy this first. |
| — | Project box, wire, heat-shrink, industrial velcro | Adhesive foam tape lets go in summer heat. |

On a **Pi 4** add adhesive heatsinks, and expect to need a 12 V lighter-socket
USB supply: it idles near 540 mA and peaks around 1.2 A booting, which many head
unit ports will not deliver. If you power it separately, **cut the VBUS wire** in
the cable to the head unit — with two sources the Pi would otherwise push 5 V
back into the stereo.

A bulk capacitor across 5 V is often suggested. It holds roughly a millisecond
at the Pi's draw, so it smooths transients but cannot rescue an undersized port,
and a discharged 2200 µF cap is effectively a short at power-on that can trip a
port with latching overcurrent protection. If you fit one, 1000 µF is the better
trade. Measure first.

## Buzzer wiring

```
                    5V  (header pin 2 or 4)
                     │
                  [BUZZER]        + to 5V, - to collector
                     │
                     ├──────── C
   GPIO18 ──[1k]──── B      (NPN)
   (pin 12)          │
                     ├──[10k]──┐
                     └──────── E
                               │
                     GND (header pin 6) ── common
```

| From | To |
|---|---|
| Buzzer **+** | Pin 2 or 4 — 5 V |
| Buzzer **−** | Transistor **collector** |
| Transistor **emitter** | Pin 6 — GND |
| Transistor **base** | 1 kΩ → Pin 12 — GPIO18 |
| Transistor **base** | 10 kΩ → GND |

The 10 kΩ is not decoration: GPIO18 is an input until userspace configures it,
and a floating base can leave the buzzer droning through early boot.
`carmp3-beep-init.service` drives the pin low at boot as well, but the resistor
covers the window before that runs.

**Do not solder to the Pi.** Build on perfboard and run three female jumpers to
pins 4, 6 and 12. The Pi stays unmodified and swappable.

GPIO18 is also PWM0, so if you end up with a **passive** buzzer you can drive a
tone on the same pin without rewiring.

## Checking the transistor before you solder

TO-92 pinouts are not consistent between part numbers, and this is the most
common way the circuit fails.

- `PN2222A`, `P2N2222A`, `2N3904` — flat face toward you: **E B C** left→right
- `BC337`, `BC547` — same orientation: **C B E**, reversed

Verify with a multimeter on diode mode rather than trusting the marking:

1. Red probe on the middle leg. For an NPN you should read ~0.6–0.7 V to **both**
   outer legs, confirming the middle leg is the base.
2. Of the two outer legs, the one with the **slightly higher** forward voltage
   from base is the **emitter**.

## Diagnosing a silent buzzer

Check the software first — `pinctrl get 18` should read `op -- pd | hi` while a
beep sounds. If the pin toggles and nothing is heard, the fault is downstream.

Then hold the pin high and measure **collector to emitter**:

| Vce | Meaning |
|---|---|
| 0.1–0.3 V | transistor is saturating properly — suspect the buzzer |
| ~5 V | not conducting at all — no base drive |
| **~1.2 V** | **partially conducting: collector and emitter are swapped** |

To test the buzzer independently, lift its **−** leg off the collector and touch
it to GND. Full 5 V, transistor out of the circuit. Silent at that point means
the buzzer is reversed, passive, or dead.

## Mounting

Glovebox or under a seat, never the dash. Even the Zero 2 W's 70 °C ceiling is
reachable on a dashboard in summer sun.
