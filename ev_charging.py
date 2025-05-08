import json
import pandas as pd
from collections import defaultdict
import numpy as np
import csv
import os

def load_inputs(cars_file, availability_file, requirements_file, prices_file):
    """Loads input data from JSON and CSV files.
    Returns:
        tuple: (cars_dict, availability DataFrame, requirements dict, price_dict).
    """
    try:
        with open(cars_file, 'r') as f:
            cars = json.load(f)
        cars_dict = {car['id']: car for car in cars}
        
        availability = pd.read_csv(availability_file, index_col='hour')
        availability.index = pd.to_numeric(availability.index, errors='coerce')
        availability = availability.transpose()
        
        with open(requirements_file, 'r') as f:
            requirements = json.load(f)
        
        prices = pd.read_csv(prices_file)
        price_dict = dict(zip(prices['hour'], prices['price_eur_per_kWh']))
        
        return cars_dict, availability, requirements, price_dict
    except Exception as e:
        print(f"Error loading inputs: {e}")
        raise

def calculate_energy_needs(cars_dict, requirements, initial_soc=0.2):
    """Calculates energy needs per car based on target SoC requirements.
    Returns:
        defaultdict: Car ID -> list of requirement dicts (energy_kwh, deadline_hour, etc.).
    """
    energy_needs = defaultdict(list)
    for car_id, reqs in requirements.items():
        if not reqs:
            continue
        capacity = cars_dict[car_id]['capacity_kWh']
        for day, time_soc in reqs.items():
            for time, target_soc in time_soc.items():
                hour = int(time.split(':')[0])
                new_day = int(day)
                deadline_hour = (new_day + 1) * 24 + hour
                energy_kwh = (target_soc - initial_soc) * capacity
                real_energy_kwh = energy_kwh / 0.95
                energy_needs[car_id].append({
                    'day': new_day,
                    'deadline_hour': deadline_hour,
                    'energy_kwh': real_energy_kwh,
                    'max_rate_kw': cars_dict[car_id]['max_charge_rate_kW'],
                    'target_soc': target_soc,
                    'prior_day_start': new_day * 24 + 18,
                    'prior_day_end': (new_day + 1) * 24
                })
    return energy_needs

def schedule_charging(energy_needs, availability, price_dict, cars_dict, num_ports=3, total_hours=168):
    """Schedules charging to meet energy needs, respecting port limits and availability.
    Returns:
        dict: Car ID -> list of (hour, power_kw, port) charging slots.
    """
    # Initialize schedule, requirements status, and port counter
    schedule = defaultdict(list)  # Car ID -> [(hour, power_kw, port)]
    requirements_met = {car_id: [False] * len(reqs) for car_id, reqs in energy_needs.items()}
    hour_port_count = defaultdict(int)  # Ports used per hour
    
    for hour in range(18, total_hours):
        if hour not in availability.columns:
            continue
        # Get available cars
        available_cars = availability.index[availability[hour] == 1].tolist()
        
        # Build car needs: (car_id, req_index, energy_kwh, max_rate_kw)
        car_needs = []
        for car_id in available_cars:
            if car_id not in energy_needs:
                # Opportunistic charging at max rate
                car_needs.append((car_id, -1, 0, cars_dict[car_id]['max_charge_rate_kW']))
                continue
            for i, req in enumerate(energy_needs[car_id]):
                if not requirements_met[car_id][i] and req['prior_day_start'] <= hour < req['prior_day_end']:
                    car_needs.append((car_id, i, req['energy_kwh'], req['max_rate_kw']))
                    break
        
        # Prioritize cars with needs (sort by energy low to high), then others (sort by max rate high to low)
        cars_with_needs = sorted([c for c in car_needs if c[2] > 0], key=lambda x: (x[2], -x[3]))
        cars_without_needs = sorted([c for c in car_needs if c[2] == 0], key=lambda x: -x[3])
        car_needs = cars_with_needs + cars_without_needs
        
        # Assign up to num_ports charging slots
        for car_id, req_index, energy_kwh, max_rate_kw in car_needs[:num_ports]:
            if hour_port_count[hour] >= num_ports:
                break
            port = hour_port_count[hour] + 1  # Next available port
            
            if energy_kwh == 0:
                # Charge at max rate
                power_kw = max_rate_kw
                print(f"Assigning {car_id} at hour {hour} with power {power_kw} kW (port {port})")
            else:
                # Calculate remaining energy needed
                energy_delivered = sum(p for h, p, _ in schedule[car_id] 
                                     if energy_needs[car_id][req_index]['prior_day_start'] <= h < energy_needs[car_id][req_index]['prior_day_end'])
                if energy_delivered >= energy_kwh:
                    requirements_met[car_id][req_index] = True
                    continue
                energy_remaining = energy_kwh - energy_delivered
                power_kw = min(max_rate_kw, energy_remaining)
                if energy_delivered + power_kw >= energy_kwh:
                    requirements_met[car_id][req_index] = True
                print(f"Hour {hour}, Port {port}: {car_id} ({power_kw:.2f} kW)")
            
            schedule[car_id].append((hour, power_kw, port))
            hour_port_count[hour] += 1
    
    return schedule

def compute_costs_and_soc(schedule, cars_dict, price_dict, energy_needs, initial_soc=0.2):
    """Computes total costs and simulates hourly SoC for each car.
    Returns:
        tuple: (cost_summary, final_soc, hourly_soc).
    """
    cost_summary = defaultdict(list)  # Car_ID -> list of (day, hour, cost)
    final_soc = {car_id: initial_soc for car_id in cars_dict}
    hourly_soc = defaultdict(list)
    
    for car_id, sessions in schedule.items():
        capacity = cars_dict[car_id]['capacity_kWh']
        current_soc = initial_soc
        session_dict = {(h, p): port for h, p, port in sessions}  # Map (hour, power_kw) to port
        
        for hour in range(18, 168):
            day = (hour - 18) // 24
            if day > 5:
                day = 5
            
            if car_id not in energy_needs and hour == 18 + day * 24:
                current_soc = initial_soc
                print(f"Resetting {car_id} SoC to {initial_soc:.2%} at hour {hour} (Day {day})")
            
            for req in energy_needs.get(car_id, []):
                if req['deadline_hour'] == hour:
                    current_soc = initial_soc
                    print(f"Resetting {car_id} SoC to {initial_soc:.2%} at deadline hour {hour} (Day {req['day']})")
                    break
            
            power_kw = next((p for h, p, _ in sessions if h == hour), 0)
            energy_to_battery = power_kw * 0.95
            current_soc = min(1.0, current_soc + energy_to_battery / capacity)
            hourly_soc[car_id].append((day, hour, power_kw, current_soc))
            
            if power_kw > 0:
                cost = power_kw * price_dict.get(hour, 0.40)
                cost_summary[car_id].append((day, hour, cost))
    
    return cost_summary, final_soc, hourly_soc

def output_results(schedule, cost_summary, final_soc, hourly_soc, energy_needs, cars_dict, price_dict):
    # Prints results and exports to CSV files.
    # Print charging schedule
    print("\nCharging Schedule:")
    for day in range(6):
        day_slots = []
        for car_id, sessions in schedule.items():
            for hour, power_kw, port in sessions:
                if (hour - 18) // 24 == day:
                    day_slots.append((hour, port, car_id, power_kw))
        if day_slots:
            print(f"Day {day}:")
            for hour in sorted(set(h for h, _, _, _ in day_slots)):
                hour_slots = [(port, car_id, power_kw) for h, port, car_id, power_kw in day_slots if h == hour]
                hour_slots.sort(key=lambda x: x[0])  # Sort by port
                formatted_hour = f"{hour % 24:02d}:00"
                slots_str = ", ".join(f"Port {port}: {car_id} ({power_kw:.2f} kW)" for port, car_id, power_kw in hour_slots)
                print(f"  Hour {formatted_hour}: {slots_str}")
    
    # Print final SoC
    print("\nFinal SoC:")
    for car_id, soc in final_soc.items():
        print(f"{car_id}: {soc:.2%}")
    
    # Print hourly SoC and power for charging hours only
    print("\nHourly SoC and Power Simulation (Charging Hours Only):")
    for car_id, data in hourly_soc.items():
        print(f"{car_id}:")
        for day in range(6):
            day_data = [(h, p, s) for d, h, p, s in data if d == day and p > 0]
            if day_data:
                print(f"  Day {day}: {[(f'{h % 24:02d}:00', f'{p:.2f} kW', f'{s:.2%}') for h, p, s in day_data]}")
    
    # Print cost summary
    print("\nCost Summary:")
    for car_id in cars_dict:
        costs = cost_summary.get(car_id, [])
        if not costs:
            print(f"{car_id}: No charging costs")
            continue
        print(f"{car_id}:")
        total_car_cost = 0
        for day in range(6):
            day_costs = [(h, c) for d, h, c in costs if d == day]
            if day_costs:
                print(f"  Day {day}:")
                day_total = 0
                for hour, cost in sorted(day_costs, key=lambda x: x[0]):
                    power_kw = next((p for h, p, _ in schedule.get(car_id, []) if h == hour), 0)
                    formatted_hour = f"{hour % 24:02d}:00"
                    print(f"    Hour {formatted_hour}: {cost:.2f} € ({power_kw:.2f} kW × {price_dict.get(hour, 0.40):.2f} €/kWh)")
                    day_total += cost
                print(f"    Total Day {day}: {day_total:.2f} €")
                total_car_cost += day_total
        print(f"  Total {car_id}: {total_car_cost:.2f} €")
    
    # Validate requirements
    print("\nRequirements Check:")
    for car_id, reqs in energy_needs.items():
        for req in reqs:
            energy_delivered = sum(p for h, p, _ in schedule.get(car_id, []) 
                                  if req['prior_day_start'] <= h < req['prior_day_end'])
            print(f"{car_id} Day {req['day']} (by hour {req['deadline_hour']}): "
                  f"Needed {req['energy_kwh']:.2f} kWh, Delivered {energy_delivered:.2f} kWh, "
                  f"Met: {energy_delivered >= req['energy_kwh']}")
    
    # Export charging schedule
    with open('output/charging_schedule.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Day', 'Hour', 'Port', 'Car_ID', 'Power_kW'])
        for car_id, sessions in schedule.items():
            for hour, power_kw, port in sessions:
                day = (hour - 18) // 24
                formatted_hour = f"{hour % 24:02d}:00"
                writer.writerow([day, formatted_hour, port, car_id, f"{power_kw:.2f}"])
    
    # Export cost summary
    with open('output/cost_summary.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Car_ID', 'Day', 'Hour', 'Cost_EUR'])
        for car_id in cars_dict:
            costs = cost_summary.get(car_id, [])
            total_car_cost = 0
            for day in range(6):
                day_costs = [(h, c) for d, h, c in costs if d == day]
                if day_costs:
                    day_total = 0
                    for hour, cost in sorted(day_costs, key=lambda x: x[0]):
                        formatted_hour = f"{hour % 24:02d}:00"
                        writer.writerow([car_id, day, formatted_hour, f"{cost:.2f}"])
                        day_total += cost
                    writer.writerow([car_id, day, '', f"{day_total:.2f}"])
                    total_car_cost += day_total
            if total_car_cost > 0:
                writer.writerow([car_id, '', 'Total', f"{total_car_cost:.2f}"])
    
    # Export final SoC
    with open('output/final_soc.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Car_ID', 'Final_SoC'])
        for car_id, soc in final_soc.items():
            writer.writerow([car_id, f"{soc:.2%}"])

def main():
    """Main function to execute the EV charging and billing system."""
    cars_dict, availability, requirements, price_dict = load_inputs(
        'input/cars.json', 'input/availability.csv', 'input/requirements.json', 'input/prices.csv'
    )
    
    energy_needs = calculate_energy_needs(cars_dict, requirements)
    
    schedule = schedule_charging(energy_needs, availability, price_dict, cars_dict)
    
    cost_summary, final_soc, hourly_soc = compute_costs_and_soc(schedule, cars_dict, price_dict, energy_needs)
    
    output_results(schedule, cost_summary, final_soc, hourly_soc, energy_needs, cars_dict, price_dict)

if __name__ == "__main__":
    main()