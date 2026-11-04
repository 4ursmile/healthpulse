"""Code-only crosswalk from the pre-2025 provinces (FAO GAUL admin-1 codes, as carried by OpenDengue / Tycho) to the 34
provincial units created by Resolution 202/2025/QH15 (effective 1 July 2025), keyed by the OCHA COD-AB v02 P-code.

GAUL is the join key because OpenDengue reuses ISO 3166-2 code VN-30 for both Gia Lai and the former Hà Tây province.
The ISO code is kept for reference. Names are never used as join keys. The `old_name` column is documentation only (it lets a reviewer read the table).
"""
from __future__ import annotations

# new P-code -> (new name, [(old GAUL code, old ISO 3166-2 code, old name), ...])
MERGER: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "VN01": ("Hà Nội", [("1012401", "VN-HN", "Hà Nội"), ("1012370", "", "Hà Tây (merged into Hà Nội 2008)")]),
    "VN04": ("Cao Bằng", [("1012375", "VN-04", "Cao Bằng")]),
    "VN08": ("Tuyên Quang", [("1012305", "VN-07", "Tuyên Quang"), ("1012453", "VN-03", "Hà Giang")]),
    "VN11": ("Điện Biên", [("1012316", "VN-71", "Điện Biên")]),
    "VN12": ("Lai Châu", [("1012359", "VN-01", "Lai Châu")]),
    "VN14": ("Sơn La", [("1012434", "VN-05", "Sơn La")]),
    "VN15": ("Lào Cai", [("1012366", "VN-02", "Lào Cai"), ("1012437", "VN-06", "Yên Bái")]),
    "VN19": ("Thái Nguyên", [("1012322", "VN-69", "Thái Nguyên"), ("1012378", "VN-53", "Bắc Kạn")]),
    "VN20": ("Lạng Sơn", [("1012358", "VN-09", "Lạng Sơn")]),
    "VN22": ("Quảng Ninh", [("1012418", "VN-13", "Quảng Ninh")]),
    "VN24": ("Bắc Ninh", [("1012424", "VN-56", "Bắc Ninh"), ("1012309", "VN-54", "Bắc Giang")]),
    "VN25": ("Phú Thọ", [("1012435", "VN-68", "Phú Thọ"), ("1012324", "VN-70", "Vĩnh Phúc"), ("1012447", "VN-14", "Hòa Bình")]),
    "VN31": ("Hải Phòng", [("1012393", "VN-HP", "Hải Phòng"), ("1012405", "VN-61", "Hải Dương")]),
    "VN33": ("Hưng Yên", [("1012459", "VN-66", "Hưng Yên"), ("1012326", "VN-20", "Thái Bình")]),
    "VN37": ("Ninh Bình", [("1012422", "VN-18", "Ninh Bình"), ("1012425", "VN-63", "Hà Nam"), ("1012329", "VN-67", "Nam Định")]),
    "VN38": ("Thanh Hóa", [("1012394", "VN-21", "Thanh Hóa")]),
    "VN40": ("Nghệ An", [("1012311", "VN-22", "Nghệ An")]),
    "VN42": ("Hà Tĩnh", [("1012406", "VN-23", "Hà Tĩnh")]),
    "VN44": ("Quảng Trị", [("1012343", "VN-25", "Quảng Trị"), ("1012441", "VN-24", "Quảng Bình")]),
    "VN46": ("Huế", [("1012409", "VN-26", "Thừa Thiên Huế")]),
    "VN48": ("Đà Nẵng", [("1012301", "VN-DN", "Đà Nẵng"), ("1012397", "VN-27", "Quảng Nam")]),
    "VN51": ("Quảng Ngãi", [("1012415", "VN-29", "Quảng Ngãi"), ("1012341", "VN-28", "Kon Tum")]),
    "VN52": ("Gia Lai", [("1012419", "VN-30", "Gia Lai"), ("1012400", "VN-31", "Bình Định")]),
    "VN56": ("Khánh Hòa", [("1012376", "VN-34", "Khánh Hòa"), ("1012342", "VN-36", "Ninh Thuận")]),
    "VN66": ("Đắk Lắk", [("1012345", "VN-33", "Đắk Lắk"), ("1012452", "VN-32", "Phú Yên")]),
    "VN68": ("Lâm Đồng", [("1012334", "VN-35", "Lâm Đồng"), ("1012428", "VN-72", "Đắk Nông"), ("1012357", "VN-40", "Bình Thuận")]),
    "VN75": ("Đồng Nai", [("1012412", "VN-39", "Đồng Nai"), ("1012427", "VN-58", "Bình Phước")]),
    "VN79": ("TP. Hồ Chí Minh", [("1012302", "VN-SG", "Hồ Chí Minh"), ("1012365", "VN-57", "Bình Dương"), ("1012369", "VN-43", "Bà Rịa–Vũng Tàu")]),
    "VN80": ("Tây Ninh", [("1012325", "VN-37", "Tây Ninh"), ("1012395", "VN-41", "Long An")]),
    "VN82": ("Đồng Tháp", [("1012385", "VN-45", "Đồng Tháp"), ("1012408", "VN-46", "Tiền Giang")]),
    "VN86": ("Vĩnh Long", [("1012348", "VN-49", "Vĩnh Long"), ("1012388", "VN-50", "Bến Tre"), ("1012355", "VN-51", "Trà Vinh")]),
    "VN91": ("An Giang", [("1012315", "VN-44", "An Giang"), ("1012402", "VN-47", "Kiên Giang")]),
    "VN92": ("Cần Thơ", [("1012318", "VN-CT", "Cần Thơ"), ("1012346", "VN-52", "Sóc Trăng"), ("1012367", "VN-73", "Hậu Giang")]),
    "VN96": ("Cà Mau", [("1012389", "VN-59", "Cà Mau"), ("1012314", "VN-55", "Bạc Liêu")]),
}

GAUL_TO_NEW: dict[str, str] = {g: new for new, (_, olds) in MERGER.items() for g, _, _ in olds}

# Macro-regions (used for seasonal terms and for the regional response institutes)
REGION = {
    **{c: "north" for c in ["VN01", "VN04", "VN08", "VN11", "VN12", "VN14", "VN15", "VN19", "VN20", "VN22", "VN24", "VN25",
                            "VN31", "VN33", "VN37"]},
    **{c: "central" for c in ["VN38", "VN40", "VN42", "VN44", "VN46", "VN48", "VN51", "VN56"]},
    **{c: "highlands" for c in ["VN52", "VN66", "VN68"]},
    **{c: "south" for c in ["VN75", "VN79", "VN80", "VN82", "VN86", "VN91", "VN92", "VN96"]},
}

# Regional preventive-medicine institutes of the Ministry of Health (real institutions) used as default team bases.
BASES = [
    {"base_id": "nihe", "name": "NIHE (Hà Nội)", "province_id": "VN01", "lat": 21.0245, "lon": 105.8412},
    {"base_id": "pasteur_nt", "name": "Pasteur Institute Nha Trang", "province_id": "VN56", "lat": 12.2470, "lon": 109.1960},
    {"base_id": "tihe", "name": "Tây Nguyên Institute (Buôn Ma Thuột)", "province_id": "VN66", "lat": 12.6800, "lon": 108.0500},
    {"base_id": "pasteur_hcm", "name": "Pasteur Institute HCMC", "province_id": "VN79", "lat": 10.7863, "lon": 106.6886},
]


def check() -> None:
    olds = [g for _, (_, o) in MERGER.items() for g, _, _ in o]
    assert len(MERGER) == 34, len(MERGER)
    assert len(olds) == 64 and len(set(olds)) == 64, (len(olds), len(set(olds)))  # 63 provinces + Hà Tây
    assert set(REGION) == set(MERGER)
