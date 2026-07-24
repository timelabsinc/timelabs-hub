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

# City -> state. It used to be a flat list of names, which meant a pasted
# address only ever produced a state if the customer had spelled the state out
# themselves — "Baner Road, Pune / 411045" resolved the city and left State
# blank, on every order, and that blank went to the sheet and the customer
# record. Knowing the city is knowing the state, so the table carries both.
CITY_STATE = {
    "Mumbai": "Maharashtra", "Navi Mumbai": "Maharashtra", "Thane": "Maharashtra",
    "Pune": "Maharashtra", "Nagpur": "Maharashtra", "Nashik": "Maharashtra",
    "Aurangabad": "Maharashtra", "Solapur": "Maharashtra", "Kolhapur": "Maharashtra",
    "Delhi": "Delhi", "New Delhi": "Delhi", "Dwarka": "Delhi",
    "Noida": "Uttar Pradesh", "Greater Noida": "Uttar Pradesh",
    "Ghaziabad": "Uttar Pradesh", "Lucknow": "Uttar Pradesh",
    "Kanpur": "Uttar Pradesh", "Varanasi": "Uttar Pradesh", "Agra": "Uttar Pradesh",
    "Meerut": "Uttar Pradesh", "Allahabad": "Uttar Pradesh",
    "Prayagraj": "Uttar Pradesh", "Bareilly": "Uttar Pradesh",
    "Aligarh": "Uttar Pradesh", "Moradabad": "Uttar Pradesh",
    "Saharanpur": "Uttar Pradesh", "Gorakhpur": "Uttar Pradesh",
    "Jhansi": "Uttar Pradesh", "Mathura": "Uttar Pradesh",
    "Faridabad": "Haryana", "Gurgaon": "Haryana", "Gurugram": "Haryana",
    "Panchkula": "Haryana", "Ambala": "Haryana", "Karnal": "Haryana",
    "Panipat": "Haryana", "Hisar": "Haryana", "Rohtak": "Haryana",
    "Sonipat": "Haryana", "Rewari": "Haryana", "Palwal": "Haryana",
    "Bahadurgarh": "Haryana", "Kurukshetra": "Haryana",
    "Bengaluru": "Karnataka", "Bangalore": "Karnataka", "Mysuru": "Karnataka",
    "Mysore": "Karnataka", "Mangalore": "Karnataka", "Hubli": "Karnataka",
    "Belgaum": "Karnataka",
    "Chennai": "Tamil Nadu", "Coimbatore": "Tamil Nadu", "Madurai": "Tamil Nadu",
    "Tiruchirappalli": "Tamil Nadu", "Salem": "Tamil Nadu",
    "Tirunelveli": "Tamil Nadu", "Vellore": "Tamil Nadu", "Erode": "Tamil Nadu",
    "Hyderabad": "Telangana", "Secunderabad": "Telangana",
    "Warangal": "Telangana", "Nizamabad": "Telangana",
    "Visakhapatnam": "Andhra Pradesh", "Vijayawada": "Andhra Pradesh",
    "Guntur": "Andhra Pradesh", "Nellore": "Andhra Pradesh",
    "Tirupati": "Andhra Pradesh",
    "Kolkata": "West Bengal", "Howrah": "West Bengal", "Durgapur": "West Bengal",
    "Asansol": "West Bengal", "Siliguri": "West Bengal",
    "Ahmedabad": "Gujarat", "Surat": "Gujarat", "Vadodara": "Gujarat",
    "Rajkot": "Gujarat", "Bhavnagar": "Gujarat", "Jamnagar": "Gujarat",
    "Gandhinagar": "Gujarat",
    "Jaipur": "Rajasthan", "Jodhpur": "Rajasthan", "Udaipur": "Rajasthan",
    "Kota": "Rajasthan", "Ajmer": "Rajasthan", "Bikaner": "Rajasthan",
    "Patna": "Bihar", "Gaya": "Bihar", "Bhagalpur": "Bihar",
    "Muzaffarpur": "Bihar",
    "Ranchi": "Jharkhand", "Jamshedpur": "Jharkhand", "Dhanbad": "Jharkhand",
    "Bhopal": "Madhya Pradesh", "Indore": "Madhya Pradesh",
    "Jabalpur": "Madhya Pradesh", "Gwalior": "Madhya Pradesh",
    "Ujjain": "Madhya Pradesh",
    "Raipur": "Chhattisgarh", "Bhilai": "Chhattisgarh", "Bilaspur": "Chhattisgarh",
    "Chandigarh": "Chandigarh",
    "Ludhiana": "Punjab", "Amritsar": "Punjab", "Jalandhar": "Punjab",
    "Patiala": "Punjab", "Bathinda": "Punjab",
    "Dehradun": "Uttarakhand", "Haridwar": "Uttarakhand",
    "Rishikesh": "Uttarakhand", "Haldwani": "Uttarakhand",
    "Shimla": "Himachal Pradesh",
    "Srinagar": "Jammu and Kashmir", "Jammu": "Jammu and Kashmir",
    "Guwahati": "Assam", "Dibrugarh": "Assam", "Silchar": "Assam",
    "Imphal": "Manipur", "Shillong": "Meghalaya", "Aizawl": "Mizoram",
    "Kohima": "Nagaland", "Agartala": "Tripura", "Itanagar": "Arunachal Pradesh",
    "Gangtok": "Sikkim",
    "Bhubaneswar": "Odisha", "Cuttack": "Odisha", "Rourkela": "Odisha",
    "Puri": "Odisha",
    "Thiruvananthapuram": "Kerala", "Kochi": "Kerala", "Ernakulam": "Kerala",
    "Kozhikode": "Kerala", "Calicut": "Kerala", "Thrissur": "Kerala",
    "Kollam": "Kerala", "Kannur": "Kerala", "Alappuzha": "Kerala",
    "Panaji": "Goa", "Vasco da Gama": "Goa", "Margao": "Goa",
    "Puducherry": "Puducherry",
    "Port Blair": "Andaman and Nicobar Islands",
}
CITIES = list(CITY_STATE)

# Coarse fallback for a town not in the table above. Indian pincodes are
# allocated by region, so the first two digits pin the state for most of the
# country. Only unambiguous ranges are listed — where a prefix genuinely
# straddles two states it's left out rather than guessed, since a wrong state
# is worse than a blank one someone fills in.
PIN_STATE = {
    "11": "Delhi", "12": "Haryana", "13": "Haryana", "14": "Punjab",
    "15": "Punjab", "16": "Punjab", "17": "Himachal Pradesh",
    "18": "Jammu and Kashmir", "19": "Jammu and Kashmir",
    "20": "Uttar Pradesh", "21": "Uttar Pradesh", "22": "Uttar Pradesh",
    "23": "Uttar Pradesh", "25": "Uttar Pradesh", "27": "Uttar Pradesh",
    "28": "Uttar Pradesh",
    "30": "Rajasthan", "31": "Rajasthan", "32": "Rajasthan", "33": "Rajasthan",
    "34": "Rajasthan", "36": "Gujarat", "37": "Gujarat", "38": "Gujarat",
    "39": "Gujarat",
    "40": "Maharashtra", "41": "Maharashtra", "42": "Maharashtra",
    "43": "Maharashtra", "44": "Maharashtra",
    "45": "Madhya Pradesh", "46": "Madhya Pradesh", "47": "Madhya Pradesh",
    "48": "Madhya Pradesh", "49": "Chhattisgarh",
    "50": "Telangana", "51": "Andhra Pradesh", "52": "Andhra Pradesh",
    "53": "Andhra Pradesh",
    "56": "Karnataka", "57": "Karnataka", "58": "Karnataka", "59": "Karnataka",
    "60": "Tamil Nadu", "61": "Tamil Nadu", "62": "Tamil Nadu",
    "63": "Tamil Nadu", "64": "Tamil Nadu",
    "67": "Kerala", "68": "Kerala", "69": "Kerala",
    "70": "West Bengal", "71": "West Bengal", "72": "West Bengal",
    "73": "West Bengal", "74": "West Bengal",
    "75": "Odisha", "76": "Odisha", "77": "Odisha",
    "78": "Assam", "80": "Bihar", "84": "Bihar",
}

_STATE_LOOKUP = {s.lower(): s for s in STATES}
_STATE_LOOKUP.update(STATE_ALIASES)
_CITY_LOOKUP = {c.lower(): c for c in CITIES}
_CITY_STATE_LOOKUP = {c.lower(): s for c, s in CITY_STATE.items()}


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


def state_for_pin(pincode):
    """State from a pincode's region prefix, for towns not in the city table."""
    digits = "".join(c for c in (pincode or "") if c.isdigit())
    return PIN_STATE.get(digits[:2]) if len(digits) == 6 else None


def parse(address, pincode=None):
    """(city, state) — either may be None.

    Three sources, most reliable first: the state written in the address, the
    state implied by a recognised city, then the pincode's region. Anything
    the customer spelled out wins, because they know where they live.
    """
    blob = " ".join(x for x in [address, pincode] if x)
    state = find_state(blob)
    city = find_city(blob, state)
    if not state and city:
        state = _CITY_STATE_LOOKUP.get(city.lower())
    if not state:
        state = state_for_pin(pincode)
    return city, state


if __name__ == "__main__":
    for a in ["123(near bharat petroleum) village morna sector 168 noida, "
              "GAUTAM BUDDHA NAGAR, UTTAR PRADESH",
              "Flat 4B, Andheri West, Mumbai, Maharashtra",
              "12 MG Road, Bengaluru, Karnataka",
              "9 Park Street, Kolkata, WB"]:
        print(parse(a), "<-", a[:50])
