#!/usr/bin/env python3
"""City/state recognition for pasted Indian addresses.

Pasted blocks rarely label city and state — they just run on:
    Address: 123 village morna sector 168 noida
             GAUTAM BUDDHA NAGAR
             UTTAR PRADESH
    Pincode: 201301

So: match the state against the official list, then take the city as the best
known-city hit, falling back to the comma-segment sitting just before the
state. Both land in editable fields, so a wrong guess costs a correction, not
a bad record.
"""
import re

STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram",
    "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu",
    "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar Islands", "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir",
    "Ladakh", "Lakshadweep", "Puducherry",
]
STATE_ALIASES = {
    "orissa": "Odisha", "pondicherry": "Puducherry", "uttaranchal": "Uttarakhand",
    "new delhi": "Delhi", "nct of delhi": "Delhi", "delhi ncr": "Delhi",
    "j&k": "Jammu and Kashmir", "up": "Uttar Pradesh", "mp": "Madhya Pradesh",
    "hp": "Himachal Pradesh", "tn": "Tamil Nadu", "wb": "West Bengal",
    "ap": "Andhra Pradesh", "ts": "Telangana", "mh": "Maharashtra",
    "ka": "Karnataka", "gj": "Gujarat", "rj": "Rajasthan", "pb": "Punjab",
    "hr": "Haryana", "br": "Bihar", "jk": "Jammu and Kashmir",
}

# Enough coverage that the common case resolves without a pincode dataset.
CITIES = [
    "Mumbai", "Navi Mumbai", "Thane", "Pune", "Nagpur", "Nashik", "Aurangabad",
    "Solapur", "Kolhapur", "Delhi", "New Delhi", "Noida", "Greater Noida",
    "Ghaziabad", "Faridabad", "Gurgaon", "Gurugram", "Bengaluru", "Bangalore",
    "Mysuru", "Mysore", "Mangalore", "Hubli", "Belgaum", "Chennai", "Coimbatore",
    "Madurai", "Tiruchirappalli", "Salem", "Tirunelveli", "Vellore", "Erode",
    "Hyderabad", "Secunderabad", "Warangal", "Nizamabad", "Visakhapatnam",
    "Vijayawada", "Guntur", "Nellore", "Tirupati", "Kolkata", "Howrah",
    "Durgapur", "Asansol", "Siliguri", "Ahmedabad", "Surat", "Vadodara",
    "Rajkot", "Bhavnagar", "Jamnagar", "Gandhinagar", "Jaipur", "Jodhpur",
    "Udaipur", "Kota", "Ajmer", "Bikaner", "Lucknow", "Kanpur", "Varanasi",
    "Agra", "Meerut", "Allahabad", "Prayagraj", "Bareilly", "Aligarh",
    "Moradabad", "Saharanpur", "Gorakhpur", "Jhansi", "Mathura", "Patna",
    "Gaya", "Bhagalpur", "Muzaffarpur", "Ranchi", "Jamshedpur", "Dhanbad",
    "Bhopal", "Indore", "Jabalpur", "Gwalior", "Ujjain", "Raipur", "Bhilai",
    "Bilaspur", "Chandigarh", "Ludhiana", "Amritsar", "Jalandhar", "Patiala",
    "Bathinda", "Panchkula", "Ambala", "Karnal", "Panipat", "Hisar", "Rohtak",
    "Sonipat", "Dehradun", "Haridwar", "Rishikesh", "Haldwani", "Shimla",
    "Srinagar", "Jammu", "Guwahati", "Dibrugarh", "Silchar", "Imphal",
    "Shillong", "Aizawl", "Kohima", "Agartala", "Itanagar", "Gangtok",
    "Bhubaneswar", "Cuttack", "Rourkela", "Puri", "Thiruvananthapuram",
    "Kochi", "Ernakulam", "Kozhikode", "Calicut", "Thrissur", "Kollam",
    "Kannur", "Alappuzha", "Panaji", "Vasco da Gama", "Margao", "Puducherry",
    "Port Blair", "Dwarka", "Rewari", "Palwal", "Bahadurgarh", "Kurukshetra",
]

_STATE_LOOKUP = {s.lower(): s for s in STATES}
_STATE_LOOKUP.update(STATE_ALIASES)
_CITY_LOOKUP = {c.lower(): c for c in CITIES}


def _boundary(needle, hay):
    return re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", hay)


def find_state(text):
    t = (text or "").lower().replace(".", " ")
    # full names first so "up" inside "udupi" can't win over "Uttar Pradesh"
    for key in sorted(_STATE_LOOKUP, key=lambda k: -len(k)):
        if len(key) <= 2:
            continue
        if _boundary(key, t):
            return _STATE_LOOKUP[key]
    for key in (k for k in _STATE_LOOKUP if len(k) <= 2):
        if _boundary(key, t):
            return _STATE_LOOKUP[key]
    return None


def find_city(text, state=None):
    t = (text or "").lower().replace(".", " ")
    best, best_at = None, -1
    for key in sorted(_CITY_LOOKUP, key=lambda k: -len(k)):
        m = _boundary(key, t)
        if m and m.start() > best_at:
            # don't let a city name that IS the state (Delhi) shadow a real one
            if state and _CITY_LOOKUP[key].lower() == state.lower() and best:
                continue
            best, best_at = _CITY_LOOKUP[key], m.start()
    if best:
        return best
    # fall back to the comma segment just before the state
    if state:
        parts = [p.strip() for p in re.split(r"[,\n]", text or "") if p.strip()]
        for i, p in enumerate(parts):
            if state.lower() in p.lower() and i:
                return parts[i - 1][:60]
    return None


def parse(address, pincode=None):
    """(city, state) — either may be None."""
    blob = " ".join(x for x in [address, pincode] if x)
    state = find_state(blob)
    return find_city(blob, state), state


if __name__ == "__main__":
    for a in ["123(near bharat petroleum) village morna sector 168 noida, "
              "GAUTAM BUDDHA NAGAR, UTTAR PRADESH",
              "Flat 4B, Andheri West, Mumbai, Maharashtra",
              "12 MG Road, Bengaluru, Karnataka",
              "9 Park Street, Kolkata, WB"]:
        print(parse(a), "<-", a[:50])
