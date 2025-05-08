# EV Charging and Billing System

## Overview
This Python script manages charging and billing for 5 electric vehicles (EVs) using 3 charging ports over a 6-day period. It schedules charging to meet daily energy requirements, tracks State of Charge (SoC), and calculates costs based on hourly prices. The system optimizes energy delivery while respecting availability, maximum charge rates, and port limits, aligning with energy system optimization goals. Outputs include:
- Charging schedule (day, hour in 00:00–24:00, port, car ID, power).
- Cost summary (hourly, daily, total costs per car).
- Final SoC per car.

## Inputs
- `cars.json`: EV details (ID, capacity_kWh, max_charge_rate_kW).
- `availability.csv`: Car availability (cars × hours, 1 = available).
- `requirements.json`: SoC targets per car and day.
- `prices.csv`: Hourly prices (€/kWh).

## Outputs
- `charging_schedule.csv`: Day, Hour, Port, Car_ID, Power_kW.
- `cost_summary.csv`: Car_ID, Day, Hour, Cost_EUR (includes daily and total costs).
- `final_soc.csv`: Car_ID, Final_SoC (%).

## Assumptions
- **Initial SoC**: All cars start at 20% SoC (e.g., target SoC - 0.2 × capacity, adjusted for 95% efficiency).
- **Charging Window**: Charging occurs daily from 18:00–23:00 (hours 18–23, 42–47, etc., over 6 days), per availability data.
- **Efficiency**: 95% charging efficiency (real energy = target energy / 0.95).
- **Prices**: Default €0.40/kWh if not specified in `prices.csv`.
- **Horizon**: 6 days, starting at 18:00 on Day 0, covering hours 18–167.

## How to Run
### Prerequisites
- Python 3.8+
- Dependencies: `pandas`, `numpy`

### Setup
1. Place input files (`cars.json`, `availability.csv`, `requirements.json`, `prices.csv`) in the same directory as `ev_charging.py`.
2. Install dependencies:
   ```bash
   pip install pandas numpy
   ```
3. Run the script:
   ```bash
   python ev_charging.py
   ```

## Key Features
- **Fair Scheduling**: Prioritizes cars with lower energy needs to maximize fulfillment, while cars without requirements (e.g., Car_5) charge opportunistically.
- **Hourly SoC Simulation**: Tracks SoC and power during charging hours (18:00–23:00) in HH:00 format, with daily SoC resets to 20% for cars like Car_5 or at requirement deadlines.
- **Precise Energy Delivery**: Ensures exact energy delivery to meet SoC targets without overcharging.
- **Cost Calculation**: Computes costs using energy delivered and hourly prices.
- **Validation**: Logs port usage (≤3 ports/hour) and verifies requirement fulfillment.

## Notes
- The script is designed for a small residential building, optimizing EV charging within constrained resources.
