"""
Generate test datasets for PredictiveForecast.
Run: python test_data/generate_test_data.py

Creates 3 dataset pairs (train + test) covering different use cases:
  1. Retail Sales Forecasting  (matches your original domain)
  2. Housing Price Prediction   (classic regression)
  3. Energy Consumption Forecast (time-series-like tabular)
"""
import os
import numpy as np
import pandas as pd

np.random.seed(42)
OUT_DIR = os.path.dirname(os.path.abspath(__file__))


# ─────────────────────────────────────────────────────────────────────────────
# Dataset 1: Retail Sales Forecasting
# Mirrors the structure of your original aero/apparel pipeline
# ─────────────────────────────────────────────────────────────────────────────
def generate_retail_sales():
    n_train = 800
    n_test = 200

    def _make_rows(n):
        divisions = [10, 20, 30]
        departments = [100, 200, 300, 400]
        classes = [1001, 1002, 1003, 2001, 2002, 3001]
        brands = ['BrandA', 'BrandB', 'BrandC', 'BrandD']
        fabrics = ['Cotton', 'Polyester', 'Denim', 'Silk', 'Linen']
        seasons = ['Spring', 'Summer', 'Fall', 'Winter']
        tiers = ['Premium', 'Mid', 'Value']
        treatments = ['Washed', 'Raw', 'Distressed', 'Garment Dyed']
        colors = ['Black', 'Blue', 'White', 'Red', 'Green', 'Navy', 'Grey']
        silhouettes = ['Slim', 'Regular', 'Relaxed', 'Oversized']

        rows = []
        for _ in range(n):
            div = np.random.choice(divisions)
            dept = np.random.choice(departments)
            cls = np.random.choice(classes)
            brand = np.random.choice(brands)
            fabric = np.random.choice(fabrics)
            season = np.random.choice(seasons)
            tier = np.random.choice(tiers)
            treatment = np.random.choice(treatments)
            color = np.random.choice(colors)
            silhouette = np.random.choice(silhouettes)
            week = np.random.randint(1, 53)
            store_count = np.random.randint(50, 500)
            base_price = round(np.random.uniform(15, 120), 2)
            promo_discount = round(np.random.choice([0, 0, 0, 0.1, 0.15, 0.2, 0.25, 0.3]), 2)

            # Sales driven by realistic factors
            base_sales = 5.0
            base_sales += {'Premium': 2, 'Mid': 4, 'Value': 7}[tier]
            base_sales += {'Cotton': 1.5, 'Polyester': 0.5, 'Denim': 2.0, 'Silk': -1, 'Linen': 0.8}[fabric]
            base_sales += {'Spring': 1.0, 'Summer': 2.5, 'Fall': 1.5, 'Winter': 0.5}[season]
            base_sales += promo_discount * 15  # promo boost
            base_sales += store_count * 0.005
            base_sales -= base_price * 0.03    # higher price = lower units
            base_sales += np.random.normal(0, 1.5)
            sales_units = max(0.1, round(base_sales, 2))

            rows.append({
                'DivId': div,
                'DeptId': dept,
                'ItmClsId': cls,
                'Brand': brand,
                'Fabric': fabric,
                'Season': season,
                'Tier': tier,
                'Treatment': treatment,
                'Color': color,
                'Silhouette': silhouette,
                'WeekNum': week,
                'StoreCnt': store_count,
                'BasePrice': base_price,
                'PromoDiscount': promo_discount,
                'SlsUnitsCnt': sales_units,
            })
        return pd.DataFrame(rows)

    train_df = _make_rows(n_train)
    test_df = _make_rows(n_test)

    train_df.to_csv(os.path.join(OUT_DIR, 'retail_sales_train.csv'), index=False)
    test_df.to_csv(os.path.join(OUT_DIR, 'retail_sales_test.csv'), index=False)
    print(f'Retail Sales: train={len(train_df)} rows, test={len(test_df)} rows, cols={list(train_df.columns)}')


# ─────────────────────────────────────────────────────────────────────────────
# Dataset 2: Housing Price Prediction
# Classic regression dataset with mixed numerical/categorical features
# ─────────────────────────────────────────────────────────────────────────────
def generate_housing():
    n_train = 600
    n_test = 150

    def _make_rows(n):
        neighborhoods = ['Downtown', 'Suburbs', 'Midtown', 'Lakeside', 'Industrial', 'University']
        house_types = ['Single Family', 'Condo', 'Townhouse', 'Duplex']
        conditions = ['Excellent', 'Good', 'Fair', 'Poor']
        garage_types = ['Attached', 'Detached', 'None']

        rows = []
        for _ in range(n):
            sqft = np.random.randint(600, 5000)
            bedrooms = np.random.choice([1, 2, 3, 4, 5], p=[0.05, 0.2, 0.4, 0.25, 0.1])
            bathrooms = np.random.choice([1, 1.5, 2, 2.5, 3, 3.5], p=[0.1, 0.15, 0.3, 0.2, 0.15, 0.1])
            year_built = np.random.randint(1950, 2024)
            lot_size = round(np.random.uniform(0.1, 2.0), 2)
            neighborhood = np.random.choice(neighborhoods)
            house_type = np.random.choice(house_types)
            condition = np.random.choice(conditions, p=[0.15, 0.45, 0.3, 0.1])
            garage = np.random.choice(garage_types, p=[0.5, 0.2, 0.3])
            has_pool = np.random.choice([0, 1], p=[0.75, 0.25])
            stories = np.random.choice([1, 2, 3], p=[0.3, 0.55, 0.15])

            # Price model
            price = 50000
            price += sqft * 120
            price += bedrooms * 15000
            price += bathrooms * 12000
            price += (year_built - 1950) * 800
            price += lot_size * 40000
            price += {'Downtown': 80000, 'Suburbs': 20000, 'Midtown': 60000,
                      'Lakeside': 100000, 'Industrial': -20000, 'University': 30000}[neighborhood]
            price += {'Single Family': 30000, 'Condo': -20000, 'Townhouse': 10000, 'Duplex': 5000}[house_type]
            price += {'Excellent': 40000, 'Good': 15000, 'Fair': -5000, 'Poor': -30000}[condition]
            price += {'Attached': 25000, 'Detached': 15000, 'None': 0}[garage]
            price += has_pool * 35000
            price += stories * 10000
            price += np.random.normal(0, 25000)
            price = max(50000, round(price, -3))

            rows.append({
                'SquareFeet': sqft,
                'Bedrooms': bedrooms,
                'Bathrooms': bathrooms,
                'YearBuilt': year_built,
                'LotSize': lot_size,
                'Neighborhood': neighborhood,
                'HouseType': house_type,
                'Condition': condition,
                'GarageType': garage,
                'HasPool': has_pool,
                'Stories': stories,
                'Price': price,
            })
        return pd.DataFrame(rows)

    train_df = _make_rows(n_train)
    test_df = _make_rows(n_test)

    train_df.to_csv(os.path.join(OUT_DIR, 'housing_train.csv'), index=False)
    test_df.to_csv(os.path.join(OUT_DIR, 'housing_test.csv'), index=False)
    print(f'Housing: train={len(train_df)} rows, test={len(test_df)} rows, cols={list(train_df.columns)}')


# ─────────────────────────────────────────────────────────────────────────────
# Dataset 3: Energy Consumption Forecasting
# Time-series-like tabular data with weather and building features
# ─────────────────────────────────────────────────────────────────────────────
def generate_energy():
    n_train = 1000
    n_test = 250

    def _make_rows(n):
        building_types = ['Office', 'Retail', 'Warehouse', 'Residential', 'Hospital']
        hvac_types = ['Central', 'Split', 'Window', 'VRF']
        insulation = ['High', 'Medium', 'Low']
        day_types = ['Weekday', 'Weekend', 'Holiday']

        rows = []
        for _ in range(n):
            building = np.random.choice(building_types)
            hvac = np.random.choice(hvac_types)
            insul = np.random.choice(insulation)
            day_type = np.random.choice(day_types, p=[0.6, 0.3, 0.1])
            month = np.random.randint(1, 13)
            hour = np.random.randint(0, 24)
            floor_area = np.random.randint(500, 20000)
            occupancy = np.random.randint(5, 500)
            outdoor_temp = round(np.random.normal(
                {1: 35, 2: 38, 3: 48, 4: 58, 5: 68, 6: 78,
                 7: 85, 8: 83, 9: 75, 10: 62, 11: 48, 12: 38}[month], 8), 1)
            humidity = round(np.random.uniform(20, 95), 1)
            solar_radiation = round(max(0, np.random.normal(
                500 if 6 <= hour <= 18 else 0, 200)), 1)
            wind_speed = round(max(0, np.random.normal(8, 5)), 1)

            # Energy model (kWh)
            energy = 50
            energy += floor_area * 0.01
            energy += occupancy * 0.15
            energy += abs(outdoor_temp - 70) * 2.5  # heating/cooling load
            energy += humidity * 0.1
            energy += {'Office': 20, 'Retail': 30, 'Warehouse': 10,
                       'Residential': 15, 'Hospital': 50}[building]
            energy += {'Central': -10, 'Split': 5, 'Window': 15, 'VRF': -5}[hvac]
            energy += {'High': -20, 'Medium': 0, 'Low': 25}[insul]
            energy += {'Weekday': 15, 'Weekend': -10, 'Holiday': -15}[day_type]
            # Peak hours
            if 8 <= hour <= 18:
                energy *= 1.4
            energy -= solar_radiation * 0.02
            energy += np.random.normal(0, 15)
            energy = max(10, round(energy, 2))

            rows.append({
                'Month': month,
                'Hour': hour,
                'DayType': day_type,
                'BuildingType': building,
                'HVACType': hvac,
                'Insulation': insul,
                'FloorArea': floor_area,
                'Occupancy': occupancy,
                'OutdoorTemp': outdoor_temp,
                'Humidity': humidity,
                'SolarRadiation': solar_radiation,
                'WindSpeed': wind_speed,
                'EnergyConsumption': energy,
            })
        return pd.DataFrame(rows)

    train_df = _make_rows(n_train)
    test_df = _make_rows(n_test)

    train_df.to_csv(os.path.join(OUT_DIR, 'energy_train.csv'), index=False)
    test_df.to_csv(os.path.join(OUT_DIR, 'energy_test.csv'), index=False)
    print(f'Energy: train={len(train_df)} rows, test={len(test_df)} rows, cols={list(train_df.columns)}')


# ─────────────────────────────────────────────────────────────────────────────
# Generate all datasets
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print('Generating test datasets...\n')
    generate_retail_sales()
    generate_housing()
    generate_energy()
    print(f'\nAll datasets saved to: {OUT_DIR}')
