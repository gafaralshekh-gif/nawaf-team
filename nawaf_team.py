# -*- coding: utf-8 -*-
"""
Nawaf Team v1.8 — Professional iPhone Repair & Diagnostic Tool
برنامج لتشخيص ريستارت الآيفون ومراقبة أخطاء 3uTools وتحليلها.

التشغيل:      python nawaf_team.py            (يفتح نافذة البرنامج)
وضع تجريبي:   python nawaf_team.py --demo     (بيانات جهاز ومحاكاة خطأ 4014)
بدون واجهة:   python nawaf_team.py --panic ملف.ips   |   python nawaf_team.py --log سجل.log
المتطلبات: Python 3.9+ فقط + متصفح Edge أو Chrome (موجود افتراضياً على ويندوز).
اختياري لقراءة الجهاز: libimobiledevice (ideviceinfo / idevicecrashreport).
"""

import os, re, sys, glob, time, json, shutil, subprocess, threading
from datetime import datetime

# ---------------------------------------------------------------- قاعدة المعرفة
# category: MEM = ذاكرة (NAND/RAM) | CPU = معالج/SoC | PWR = تغذية/باتري/شحن | USB = كابل/منفذ | SW = برمجي
CAT_AR = {
    "MEM": "الذاكرة (NAND / RAM)",
    "CPU": "المعالج / SoC",
    "PWR": "التغذية (PMU / باتري / دائرة الشحن)",
    "USB": "الكابل / منفذ USB / الاتصال",
    "SW":  "برمجي (نظام / ملفات)",
    "BB":  "الباسباند / المودم",
    "FLX": "الفلاتات والأجزاء الطرفية (شحن / قرب / زر / صوت / باتري)",
    "DSP": "الشاشة / دارة اللمس (MTP)",
}

PANIC_RULES = [
    # المصدر: iFixit "iPhone Kernel Panics" — المعنى كما ورد بالصفحة حرفياً (بتصرف بالترجمة فقط)
    (r"Watchdog Timeout.{0,40}No Check In|no successful checkins", "SW", 2, "Watchdog: لم تُسجَّل check-in ضمن الحد الأقصى (عادةً 180 ث). iFixit: لو ظهر اسم عملية برمجية (Springboard / logd / wifid / thermalmonitord بدون ذكر حساس) فغالباً برمجي."),
    (r"thermalmonitord.{0,200}missing sensor|missing sensor.{0,200}thermalmonitord", "FLX", 4, "Watchdog Thermalmonitord Missing Sensor: يعيد الجهاز التشغيل إذا لم يسمع من حساساته نحو 3 دقائق؛ الحساس المذكور بالسجل يحدد القطعة المشتبه بها."),
    (r"\bi2c\b", "PWR", 1, "ذُكر i2c: عطل اتصال على قناة i2c. iFixit: لا يوجد مشتبه واحد؛ يتوقف على القطع الموصولة بالقناة وتختلف بين الموديلات."),
    (r"AOP NMI POWER", "FLX", 3, "AOP NMI POWER: مقاطعة من معالج Always On. iFixit: غالباً مرتبطة بكابل زر الباور أو الكاميرا الأمامية."),
    (r"AOP Panic - K2 - Bosch control channel write failure", "FLX", 3, "AOP Bosch control channel: iFixit: يحدث عادةً أثناء الصوت؛ السبب الأكثر شيوعاً تلف فلكس الشحن (يحمل إشارات السماعة). افحص وجود سوائل."),
    (r"ANS2 Recoverable Panic", "MEM", 4, "ANS2 Recoverable Panic: ANS2 متحكم التخزين. iFixit: المشتبه شريحة NAND أو خطوط اتصالها."),
    (r"AppleSocHot: Hot Hot Hot", "CPU", 4, "AppleSocHot: iFixit: خط كهربائي بين شريحة الباور والمعالج، شبه مؤكد مشكلة لوحة؛ الواي فاي والصوت غالباً سبب الحرارة."),
    (r"SEP ROM Boot Panic", "CPU", 4, "SEP ROM Boot Panic: iFixit: تلف في ROM الـ Secure Enclave أو اتصاله بالمعالج. بيانات SEP فريدة ولا يمكن إعادة إنشائها."),
    (r"Undefined Kernel Instruction", "SW", 2, "Undefined Kernel Instruction: iFixit: غالباً برمجي (حدّث/أعد التثبيت)؛ إن استمر فيُشتبه بـ RAM أو NAND."),
]
# المصدر: iFixit "iPhone SMC Panic Assertion Failed" + repair.wiki (How To Fix an iPhone XX That Randomly Restarts).
# كل قيمة مذكورة بمصدر؛ المصدران يختلفان بحساس القرب على 13 فأدخلت القيمتين. معنى القيمة يختلف بين الموديلات.
# على 16 تُكتب القيم عشرية في الـ panic، وعلى 13–15 هكس. القطعة لازم تكون مركّبة وإلا يعيد الجهاز التشغيل.
_P13 = {"iPhone14,4", "iPhone14,5", "iPhone14,2", "iPhone14,3"}
_P14 = {"iPhone14,7", "iPhone14,8"}
_P14P = {"iPhone15,2", "iPhone15,3"}
_P15 = {"iPhone15,4", "iPhone15,5"}
_P15P = {"iPhone16,1", "iPhone16,2"}
_P16 = {"iPhone17,3", "iPhone17,4"}
_P16P = {"iPhone17,1", "iPhone17,2"}
DECIMAL_PRODUCTS = _P16 | _P16P
SENSOR_GROUPS = [
    ("iPhone 13", _P13, {
        0x800: ("فليكس الشحن", ""), 0x1000: ("Proximity / Front sensor", "قيمة repair.wiki؛ iFixit تذكر 0x10000"),
        0x10000: ("Proximity / Front sensor", "قيمة iFixit؛ repair.wiki تذكر 0x1000"), 0x40000: ("الباتري", ""),
        0x400: ("Gyro (13 mini: اللوحة السفلية)", "")}),
    ("iPhone 14 / 14 Plus", _P14, {
        0x100000: ("فليكس الشحن", ""), 0x400000: ("فليكس / ملف الشحن اللاسلكي", ""), 0x200000: ("Proximity / Front sensor", ""),
        0x500000: ("الباتري", "repair.wiki: نفس الكود مذكور أيضاً لـ Taptic و/أو فليكس الشحن"),
        0x20000: ("Interposer (الطبقة الوسطى غالباً منفصلة)", "")}),
    ("iPhone 14 Pro / Pro Max", _P14P, {
        0x40000: ("فليكس الشحن", ""), 0x100000: ("فليكس الباور", ""), 0x80000: ("Proximity / Front sensor", ""),
        0x41: ("الباتري (بيانات)", "نسبة تتبدل أو رسالة non-genuine"), 0x20000: ("Gyro (اللوحة السفلية / sandwich)", "")}),
    ("iPhone 15 / 15 Plus", _P15, {
        0x80000: ("فليكس الشحن", "معه ممكن حساس الضغط"), 0x200000: ("فليكس / ملف الشحن اللاسلكي", ""),
        0x100000: ("Proximity / Front sensor", ""), 0xa1: ("الباتري", "")}),
    ("iPhone 15 Pro / Pro Max", _P15P, {
        0x300000: ("فليكس الشحن", ""), 0x400000: ("فليكس / ملف الشحن اللاسلكي", ""), 0xa1: ("الباتري", "")}),
    ("iPhone 16 / 16 Plus (عشري)", _P16, {
        1572864: ("فليكس الشحن", "مرتبط أيضاً بحساس الضغط"), 524288: ("فليكس الشحن", ""),
        2097152: ("فليكس / ملف الشحن اللاسلكي", "repair.wiki: بدونه ريستارت كل 3 دقائق"), 169: ("الباتري (خط البيانات)", "الجهاز غالباً عالق بـ 1% أو 0% أثناء الشحن")}),
    ("iPhone 16 Pro / Pro Max (عشري)", _P16P, {
        3145728: ("فليكس الشحن", "على 16 Pro Max مرتبط بمايك سفلي / حساس ضغط"), 169: ("الباتري (خط البيانات)", "")}),
]
LEGACY_SENSORS = {
    "tg0b": "البطارية (أو كونكتورها / خطوط بياناتها)", "tg0v": "البطارية؛ على 11 Pro / Pro Max أيضاً فلكس الشحن ودائرة الشحن",
    "prs0": "فلكس الشحن (حساس الضغط/البارومتر)", "mic1": "فلكس الشحن (المايك السفلي)",
    "mic2": "فلكس الباور / كاميرا الفلاش (المايك الخلفي)", "eiger": "منفذ الشحن (مذكور بصفحة 12 فقط)",
}
# ---- أكواد مجرّبة بالفصل الفعلي للقطع على موديلات محددة فقط (لها الأولوية على الجداول الأخرى) ----
_D13P = " إذا بقي الريستارت بعد تبديل الفليكس: المسار من مايك فليكس الشحن عبر الكونكتور إلى فلتر ومكثفات على البورد ثم البور (PMU). قيس دايود على المسار: السليم حوالي {d} V، وOL يعني قطع على البورد{j}. قيس كمان D+ وD- على كونكتور الشحن (السليم حوالي {u} V)، وإذا طلع OL افحص الكونكتور و{ic}."
TESTED_SENSORS = [
    ("iPhone 13 Pro", {"iPhone14,2"}, {
        0x800: ("فليكس الشحن", _D13P.format(d="0.663", j="؛ نقطة الجمبر على الوجه المقابل لكونكتور الشحن", u="0.49–0.51", ic="U9300"))}),
    ("iPhone 14 Plus", {"iPhone14,8"}, {
        0x100000: ("فليكس الشحن", ""), 0x200000: ("فليكس حساس القرب", ""), 0x300000: ("فليكس الشحن + فليكس حساس القرب", ""),
        0x400000: ("فليكس الشحن اللاسلكي", ""), 0x500000: ("فليكس الشحن + فليكس الشحن اللاسلكي", ""),
        0x600000: ("فليكس الشحن اللاسلكي + فليكس حساس القرب", ""), 0x700000: ("فليكس الشحن + الشحن اللاسلكي + حساس القرب", "")}),
    ("iPhone 14 Pro", {"iPhone15,2"}, {
        0x40000: ("فليكس الشحن", _D13P.format(d="0.395", j="", u="0.58–0.66", ic="U9300")),
        0x80000: ("فليكس حساس القرب", ""), 0xC0000: ("فليكس الشحن + فليكس حساس القرب", ""),
        0x1400000: ("مسار مايك فليكس الشحن على البورد (الفلتر والمكثفات حتى البور PMU)", " قيس دايود على المسار: السليم حوالي 0.395 V، وOL يعني قطع على البورد ويمكن تجاوزه بجمبر."),
        0x1800000: ("شريحة حساس القرب", " المسار: كونكتور حساس القرب على البورد، حساس القرب، حساس الإضاءة، وشريحة التحكم بالحساسات. إذا كان الضرر على المسار يمكن تجاوزه بجمبر إلى كونكتور حساس القرب.")}),
    ("iPhone 15 Pro", {"iPhone16,1"}, {
        0x100000: ("فليكس الشحن", _D13P.format(d="0.630", j="", u="0.72–0.73", ic="U9500")),
        0x200000: ("فليكس حساس القرب / فليكس الشحن", ""), 0x300000: ("فليكس الشحن + فليكس حساس القرب", ""),
        0x400000: ("فليكس الشحن اللاسلكي", " كونكتوره على الطبقة السفلية، فلازم تكون الطبقتين مركبتين."),
        0x500000: ("فليكس الشحن + فليكس الشحن اللاسلكي", ""), 0x600000: ("فليكس الشحن اللاسلكي + فليكس حساس القرب", ""),
        0x700000: ("فليكس الشحن + الشحن اللاسلكي + حساس القرب", ""),
        0x1000000: ("مسار مايك فليكس الشحن على البورد (الفلتر والمكثفات حتى البور PMU)", " قيس دايود على المسار: السليم حوالي 0.630 V، وOL يعني قطع على البورد ويمكن تجاوزه بجمبر.")}),
]
def tested_sensor(product, value):
    for gname, prods, table in TESTED_SENSORS:
        if product in prods and value in table:
            return gname, table[value]
    return None
def decode_sensor(product, value):
    """يرجع (اسم المجموعة، [(الجزء، ملاحظة)]) أو None إن لم يُعرف الموديل. القيم المركّبة تُفكّ بعمليات OR."""
    for gname, prods, table in SENSOR_GROUPS:
        if product in prods:
            if value in table:
                return gname, [table[value]]
            cand = [k for k in table if k & ~value == 0]
            acc = 0
            for k in cand:
                acc |= k
            if cand and acc == value:
                return gname, [table[k] for k in sorted(cand)]
            return gname, []
    return None

# ---- مرجع منفصل: أدلة Mobile Saviour (إنستغرام @mobile.saviour) كما وصلت كصور من المستخدم ----
# تُدخل فقط الأكواد التي ظهر لها نص حل مقروء في الصورة.
# لا يدخل في قواعد الأعطال ولا في النسب؛ يظهر فقط كمربع مرجعي مستقل. الدليل نفسه يقول: الأكواد إشارات وليست تشخيصاً نهائياً.
# القيمة المقروءة من الصورة تأكدت مطابقتها لمكافئها العشري المكتوب بجانبها. الخانات غير الواضحة في الصورة تُركت فارغة.
_G_STD = "تأكد أنه موصول وغير ممزق. افحص ضرر السوائل أو الفليكس المعيب. جرّب فليكس OEM أو Premium. Diode mode على كونكتورات FPC."
_G_BAT = "افحص دبابيس كونكتور البطارية (قد تنثني). Diode mode ومقارنة بلوحة سليمة. جرّب بطارية سليمة. افحص الكونكتور."
MS_GUIDE = [
    ("iPhone 16 / 16 Plus", {"iPhone17,3", "iPhone17,4"}, {
        0x180000: ("Charging Port Flex / Air Pressure Sensor", "تأكد أن الفليكس ووحدة المايك السفلي/حساس الضغط موصولة وسليمة، وجرّب قطعة OEM/Premium سليمة."),
        0x80000: ("Charging Port Flex / Air Pressure Sensor", "تأكد من الاتصال وافحص الفليكس/الوحدة وFPC من تلف أو تآكل."),
        0x200000: ("Wireless Charging Flex (Back Glass)", "تأكد من الاتصال والتلف والسوائل، وجرّب قطعة سليمة."),
        0xA9: ("Battery Data Line", "افحص دبابيس كونكتور البطارية، جرّب بطارية سليمة، وخطوط البيانات بـ diode mode."),
        0x380000: ("Charging-port + Wireless-charging flex أو Sandwich/CPU Separation", "افحص الفليكسات وقيمة نقطة القياس المعنية (حوالي 0.350؛ OL قد يدل على انفصال).")}),
    ("iPhone 15 Pro / Pro Max", {"iPhone16,1", "iPhone16,2"}, {
        0x200000: ("Proximity Flex / Charging Port Flex", _G_STD), 0x300000: ("Charging Port Flex", _G_STD), 0x100000: ("Charging Port Flex", _G_STD),
        0x400000: ("Wireless Charging Flex (Back Glass)", _G_STD), 0x600000: ("Proximity Flex + Wireless Charging Flex", _G_STD),
        0x700000: ("Charging Port Flex + Wireless Charging Flex", _G_STD), 0xA1: ("Battery Data Issue", _G_BAT)}),
    ("iPhone 15 / 15 Plus", {"iPhone15,4", "iPhone15,5"}, {
        0x200000: ("Wireless Charging Flex (Back Glass)", _G_STD), 0x80000: ("Charging Port Flex و/أو Air Pressure Sensor", _G_STD),
        0x100000: ("Proximity Flex Cable", _G_STD), 0x280000: ("Wireless Charging Flex + Charging Port Flex", _G_STD),
        0x380000: ("Wireless Charging Flex + Charging Port Flex + Proximity Flex", _G_STD), 0xA1: ("Battery Data Issue", _G_BAT)}),
    ("iPhone 14 Pro / Pro Max", {"iPhone15,2", "iPhone15,3"}, {
        0x80000: ("Proximity Flex", _G_STD), 0x40000: ("Charging Port Flex", _G_STD), 0x100000: ("Power Button Flex", _G_STD),
        0xC0000: ("Prox Flex + Charging Port Flex", _G_STD), 0x180000: ("Prox Flex + Power Button Flex", _G_STD),
        0x140000: ("Power Button + Charging Port Flex", _G_STD), 0x1C0000: ("Charging Port + Power Button + Prox Flex", _G_STD)}),
]
_B11 = "البطارية معطوبة أو فاشلة. كونكتور البطارية تالف أو ملتوي أو مكسور. افحص كونكتور البطارية على فليكس الشحن. خطوط بيانات كونكتور البطارية مفقودة: افحص دبوس بيانات البطارية (PMU_TO_BATTERY_SWI) بقراءة diode mode."
_C11 = "السبب فليكس الشحن. جرّب فليكس شحن سليم. افحص مقاومات تالفة أو ناقصة حول كونكتور منفذ الشحن."
_W11 = "مرتبط بمايك على فليكس الباور. جرّب فليكس باور سليم. افحص أضرار الفتح (pry) على كل المكونات الصغيرة قرب الكونكتور."
MS_GUIDE.append(("iPhone 17 series", {"iPhone18,*"}, {
    1048576: ("Power Flex, Volume Flex, i2c", ""), 2097152: ("Tail Plug / Speaker", ""), 3145728: ("Camera Module Bus", ""),
    4194304: ("Compass, Gyroscope, Barometer", ""), 5242880: ("Barometer 0", ""), 6291456: ("Temp Sensor, Light Sensor, Tail Flex", ""),
    11033531: ("System Bug", ""), 14680064: ("Tail Plug - Back Cover", "")}))
# أنماط نصية لسلسلة 17 (من نفس المنشور)
MS_STR17 = [(r"ANS2", "PanicString-ANS2", "Disk Data Anomaly"), (r"Base[- ]?Socd|\bSocd\b", "Panic+Base-Socd", "Cpu Core Error Report")]
_R14 = "تأكد أن الفليكس موصول. تأكد أنه غير ممزق أو متضرر بالسوائل أو معيب. جرّب OEM أو Premium (الفليكسات غير الأصلية نسبة عيبها عالية). Diode mode على كونكتورات FPC."
MS_GUIDE.append(("iPhone 14 / 14 Plus", {"iPhone14,7", "iPhone14,8"}, {
    0x100000: ("Charging Port Flex", "عند فصله يعيد الجهاز التشغيل كل 3 دقائق. " + _R14),
    0x400000: ("Back Glass / Wireless Charging Flex", "عند فصله يعيد الجهاز التشغيل كل 3 دقائق. تأكد أنه موصول ثم اتبع الفحص: " + _R14),
    0x200000: ("Proximity Flex Cable", _R14),
    0x600000: ("Wireless Charging Flex + Proximity Flex Cable", "جرّب فليكس أصلي سليم. الاثنان لازم يكونوا موصولين باللوحة."),
    0x500000: ("مشكلة اتصال مع البطارية (أو Taptic Engine وفليكس الشحن)", "اتصال البطارية يتقطع أثناء الشحن والريستارت كل 3 دقائق و30 ثانية تقريباً. جرّب بطارية ثانية. افحص قيم diode على كونكتور البطارية باللوحة. وإن لم تكن البطارية: " + _R14 + " وممكن تكون مشكلة sandwich."),
    0x20000: ("Logic board: غالباً انفصال interposer (الطبقة الوسطى بين طبقتي اللوحة)", "افحص حواف اللوحة لوجود فجوات بين الطبقات. غالباً يحتاج إصلاح لوحة متقدم وأدوات تسخين مخصصة.")}))
MS_SRC = {}
def _ms_match(product, prods):
    return product in prods or any(x.endswith("*") and product.startswith(x[:-1]) for x in prods)
MS_GUIDE_LEGACY = [
    ("iPhone 11 / 11 Pro / 11 Pro Max", {"iPhone12,1", "iPhone12,3", "iPhone12,5"}, {
        "tg0b": ("Battery (البطارية / كونكتورها / خط بياناتها)", _B11), "tg0v": ("Battery (البطارية / كونكتورها / خط بياناتها)", _B11),
        "prs0": ("Charging port flex", _C11), "mic1": ("Charging port flex", _C11), "mic2": ("Power button flex", _W11)}),
]
def ms_guide_lookup(product, value):
    for gname, prods, table in MS_GUIDE:
        if _ms_match(product, prods) and value in table:
            part, chk = table[value]
            return {"model": gname, "part": part, "check": chk, "src": ""}
    return None
def ms_guide_legacy(product, token):
    for gname, prods, table in MS_GUIDE_LEGACY:
        if product in prods and token.lower() in table:
            part, chk = table[token.lower()]
            return {"model": gname, "part": part, "check": chk, "src": ""}
    return None

# أكواد أخطاء الاستعادة (iTunes/3uTools)
RESTORE_CODES = {}  # فارغة عمداً: لا أكواد غير محققة
LOG_KEYWORDS = []

def _score(cats):
    tot = sum(cats.values()) or 1
    return sorted(((c, v, round(100 * v / tot)) for c, v in cats.items()), key=lambda x: -x[1])

SHOW_CODES = False  # False: لا تُعرض أرقام الحساسات بالشاشة (الجزء المشتبه والحل فقط). غيّرها إلى True للتحقق.
def _redact(t):
    if SHOW_CODES:
        return t
    t = re.sub(r"(missing sensors?(?:\(s\))?:?\s*)([A-Za-z0-9, ]+)", r"\1[مخفي]", t, flags=re.I)
    return re.sub(r"(sensor array[^\n]{0,40}?\bis\s+)((?:(?:0x[0-9a-fA-F]+|\d+)[ ,]*)+)", r"\1[مخفي] ", t)

def analyze_panic(text):
    cats, notes, guide = {}, [], []
    for rx, cat, w, msg in PANIC_RULES:
        m = re.search(rx, text, re.I if rx.islower() else 0)
        if m:
            groups = [g for g in m.groups() if g]
            arg = (groups[-1] if groups else "").strip()
            notes.append((cat, msg.format(arg)))
            cats[cat] = cats.get(cat, 0) + w
    pm = re.search(r'"product"\s*:\s*"(iPhone\d+,\d+)"', text)
    product = pm.group(1) if pm else ""
    tm = re.search(r"target[\\/]+([a-z]\d{2,3}[a-z_]*)[\\/]", text)
    target = tm.group(1) if tm else ""
    # فك قيم S.sensor array
    sens = []
    for sm in re.finditer(r"S\.sensor array\s+\d+\s*-\s*\d+\s+is\s+((?:(?:0x[0-9a-fA-F]+|\d+)[ ,]*)+)", text):
        for v in re.findall(r"0x[0-9a-fA-F]+|\d+", sm.group(1)):
            if v.lower().startswith("0x"):
                n = int(v, 16)
            elif product in DECIMAL_PRODUCTS or int(v) >= 0x800:
                n = int(v)    # iOS الأحدث يطبع القيم عشرية حتى على 15 Pro؛ القيم الصغيرة ملتبسة فتُهمل خارج جدول العشري
            else:
                continue
            if n and n not in [s[0] for s in sens]:
                sens.append((n, v))
    if product.startswith("iPhone18,"):
        for rx, label, part in MS_STR17:
            if re.search(rx, text, re.I):
                guide.append({"model": "iPhone 17 series", "part": part, "check": "", "src": "", "value": label if SHOW_CODES else "", "hexv": ""})
    for n, v in sens:
        t = tested_sensor(product, n) if product else None
        if t:
            lead = f"قيمة الحساس {v} ({t[0]}) → {t[1][0]}" if SHOW_CODES else f"الجزء المشتبه ({t[0]}): {t[1][0]}"
            notes.append(("FLX", lead + ". القطعة لازم تكون مركّبة وسليمة؛ المعنى خاص بهذا الموديل." + t[1][1]))
            cats["FLX"] = cats.get("FLX", 0) + 6
            continue
        g = ms_guide_lookup(product, n) if product else None
        if g:
            guide.append(dict(g, _n=n, value=v if SHOW_CODES else "", hexv=hex(n) if SHOW_CODES else ""))
        dec = decode_sensor(product, n) if product else None
        if dec and dec[1]:
            parts = " + ".join(p for p, _ in dec[1])
            extra = " ".join(x for _, x in dec[1] if x)
            if not SHOW_CODES:
                extra = " ".join(x.replace("repair.wiki: ", "") for _, x in dec[1] if x and "قيمة" not in x)
            lead = f"قيمة الحساس {v} ({dec[0]}) → {parts}" if SHOW_CODES else f"الجزء المشتبه ({dec[0]}): {parts}"
            notes.append(("FLX", lead + ". القطعة لازم تكون مركّبة وسليمة؛ المعنى خاص بهذا الموديل." + (f" ملاحظة: {extra}" if extra else "")))
            cats["FLX"] = cats.get("FLX", 0) + 6
        elif dec and guide and guide[-1].get("_n") == n:
            pass  # المرجع وجد الجزء؛ لا حاجة لسطر "غير موجود" المتناقض
        elif dec:
            notes.append(("FLX", (f"قيمة الحساس {v} غير موجودة في جدول iFixit/repair.wiki لـ {dec[0]}." if SHOW_CODES else f"حساس غير موجود في قاعدة {dec[0]}.") + (" (انظر المرجع أدناه)" if guide and guide[-1].get("_n") == n else "")))
        else:
            notes.append(("FLX", (f"قيمة الحساس {v} موجودة، لكن " if SHOW_CODES else "يوجد حساس مفقود، لكن ") + "الموديل غير ظاهر في السجل لفكّها. حدّد الموديل وراجع جدول الحساسات في مكتبة الأعطال."))
    # حساسات الموديلات الأقدم
    mm = re.search(r"missing sensors?(?:\(s\))?:?\s*([A-Za-z0-9, ]+)", text, re.I)
    if mm:
        for tok in re.findall(r"[A-Za-z0-9]+", mm.group(1)):
            part = LEGACY_SENSORS.get(tok.lower())
            g = ms_guide_legacy(product, tok) if product else None
            if g:
                guide.append(dict(g, value=tok if SHOW_CODES else "", hexv=""))
            if part:
                notes.append(("FLX", (f"الحساس {tok} → {part}" if SHOW_CODES else f"الجزء المشتبه: {part}") + " (iPhone 12 وما قبل)."))
                cats["FLX"] = cats.get("FLX", 0) + 5
    m = re.search(r'"panicString"\s*:\s*"([^"]{0,500})', text) or re.search(r"panic\(cpu[^\n]{0,500}", text) or re.search(r"panic\(.*?\):\s*(.{0,300})", text)
    head = (m.group(1) if m and m.groups() else (m.group(0) if m else ""))
    if not head:
        body = text.split("\n", 1)[1] if "\n" in text else text
        head = "(لم يُعثر على panicString) " + re.sub(r"\s+", " ", body)[:400]
    return {"summary": _redact(head), "scores": _score(cats), "notes": notes, "product": product, "target": target,
            "sensors": [v for _, v in sens], "guide": guide}

def analyze_log_line(line):
    out = []
    for m in re.finditer(r"(?:error|err(?:or)?\s*code|code|failed)[^\d\-]{0,20}(-?\d{1,5})", line, re.I):
        code = m.group(1).lstrip("-")
        if code in RESTORE_CODES:
            cat, msg = RESTORE_CODES[code]
            out.append((cat, f"كود {code}: {msg}"))
    for rx, cat, msg in LOG_KEYWORDS:
        if re.search(rx, line, re.I):
            out.append((cat, msg))
    return out

def format_panic(res):
    L = []
    if res["summary"]:
        L.append("ملخص الـ panic: " + res["summary"][:200])
    if not res["notes"]:
        L.append("لم تُطابق أي قاعدة. أرسل السجل لمراجعة يدوية.")
    for cat, v, pct in res["scores"]:
        L.append(f"  • {CAT_AR[cat]}: {pct}%")
    for cat, msg in res["notes"]:
        L.append(f"- [{CAT_AR[cat]}] {msg}")
    for g in res.get("guide", []):
        L.append(f"[مرجع — {g['model']}] " + (f"{g['value']}" + (f" ({g['hexv']})" if g['hexv'] else "") + " → " if g['value'] else "الجزء المشتبه: ") + g['part'] + (f"\n   الحل: {g['check']}" if g['check'] else ""))
    if res.get("guide"):
        L.append("(المرجع إشارات وليس تشخيصاً نهائياً)")
    L.append("\nتنبيه: التحليل استدلالي من السجلات؛ التأكيد النهائي بقياس التيار/الفولت على البورد.")
    return "\n".join(L)

# ---------------------------------------------------------------- جلب السجلات من الجهاز
def find_tool(name):
    """يبحث عن أداة libimobiledevice في PATH ثم بجانب أدوات الجهاز ثم مجلدات شائعة."""
    exe = shutil.which(name)
    if exe:
        return exe
    dirs = []
    for other in ("idevice_id", "ideviceinfo", "idevicesyslog"):
        o = shutil.which(other)
        if o:
            dirs.append(os.path.dirname(o))
    here = os.path.dirname(os.path.abspath(__file__))
    dirs += [here, os.path.join(here, "tools"), os.path.join(here, "libimobiledevice"), os.path.join(here, "idevicerestore"),
             "C:\\msys64\\ucrt64\\bin", "C:\\msys64\\mingw64\\bin"]
    for b in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""), "C:\\", "D:\\"):
        if b:
            dirs += glob.glob(os.path.join(b, "libimobiledevice*")) + glob.glob(os.path.join(b, "3uTools*")) + glob.glob(os.path.join(b, "3uTools*", "*"))
    for d in dirs:
        for ext in ("", ".exe"):
            f = os.path.join(d, name + ext)
            if os.path.isfile(f):
                return f
    return None

def _pmd3_ok():
    try:
        r = subprocess.run([sys.executable, "-m", "pymobiledevice3", "--help"], capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False

def _clean_empty(dest):
    """يحذف ملفات 0 بايت من مجلد السحب (بقايا سحب سابق انقطع) حتى لا تمنع الأداة من إعادة نسخها."""
    n = 0
    for dp, dn, fn in os.walk(dest):
        for f in fn:
            fp = os.path.join(dp, f)
            try:
                if os.path.getsize(fp) == 0:
                    os.remove(fp)
                    n += 1
            except OSError:
                pass
    return n

def _only_empty_panics(dest):
    """True إذا وُجدت ملفات panic كلها فارغة (0 بايت)."""
    ps = [f for f in glob.glob(os.path.join(dest, "**", "*panic*"), recursive=True) if os.path.isfile(f)]
    return bool(ps) and all(os.path.getsize(f) == 0 for f in ps)

def pull_crash_reports(dest):
    """يسحب تقارير الكراش/panic من الجهاز مباشرة. يرجع (نجاح، رسالة، ناقص_أداة)."""
    os.makedirs(dest, exist_ok=True)
    _clean_empty(dest)
    exe = find_tool("idevicecrashreport")
    log = []
    if exe:
        for args in (["-e", "-k"], ["-k"]):
            r = subprocess.run([exe] + args + [dest], capture_output=True, text=True, timeout=300)
            log.append(" ".join(args) + f" → rc={r.returncode} {(r.stdout + r.stderr)[-200:]}")
            if not _only_empty_panics(dest):
                return r.returncode == 0, "\n".join(log), False
            _clean_empty(dest)
    if _pmd3_ok():
        r = subprocess.run([sys.executable, "-m", "pymobiledevice3", "crash", "pull", dest], capture_output=True, text=True, timeout=300)
        log.append(f"pymobiledevice3 → rc={r.returncode} {(r.stdout + r.stderr)[-200:]}")
        return r.returncode == 0, "\n".join(log), False
    if exe:
        return False, "الأداة سحبت الملفات لكنها وصلت فارغة (0 بايت). جرّب أداة السحب البديلة.\n" + "\n".join(log), True
    return False, "أداة سحب السجلات غير موجودة على هذا الكمبيوتر.", True

def install_pull_tool():
    """تثبيت pymobiledevice3 بضغطة المستخدم (يحتاج إنترنت)."""
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "pymobiledevice3"], capture_output=True, text=True, timeout=900)
    return r.returncode == 0, (r.stdout + r.stderr)[-600:]

def _is_panic_file(f):
    n = os.path.basename(f).lower()
    if "panic" in n:
        return True
    try:
        with open(f, "r", errors="ignore") as fh:
            return '"bug_type":"210"' in fh.read(600).replace(" ", "")
    except Exception:
        return False

def find_panics(folder, with_skipped=False):
    """ملفات panic الحقيقية فقط (panic-full…). تقارير التطبيقات (Jetsam, cpu_resource, Siri…) ليست ريستارت."""
    files = set()
    for p in ("*.ips", "*.panic", "panic-full*", "*Panic*"):
        files.update(glob.glob(os.path.join(folder, "**", p), recursive=True))
    def _k(f):
        m = re.search(r"(\d{4}-\d{2}-\d{2}-\d{6})", os.path.basename(f))
        return (m.group(1) if m else "", os.path.getmtime(f))
    files = sorted(files, key=_k, reverse=True)  # الأحدث أولاً حسب الوقت المكتوب باسم الملف (وقت الهاتف)، ثم تاريخ الملف
    good = [f for f in files if _is_panic_file(f)]
    return (good, len(files) - len(good)) if with_skipped else good

# ---------------------------------------------------------------- مراقب 3uTools
def candidate_log_dirs():
    pf = [os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""), "C:\\", "D:\\"]
    ap = [os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", ""), os.path.expanduser("~")]
    c = []
    for b in pf:
        c += [os.path.join(b, "Aisi", "3uTools", "Log"), os.path.join(b, "3uTools", "Log"), os.path.join(b, "3uTools", "logs")]
    for b in ap:
        c += [os.path.join(b, "3uTools"), os.path.join(b, "Aisi", "3uTools")]
    return [d for d in c if d and os.path.isdir(d)]

class LogWatcher(threading.Thread):
    def __init__(self, folder, callback):
        super().__init__(daemon=True)
        self.folder, self.cb, self.stop_flag = folder, callback, False
        self.pos = {}

    def newest(self):
        fs = [f for f in glob.glob(os.path.join(self.folder, "**", "*"), recursive=True)
              if os.path.isfile(f) and f.lower().endswith((".log", ".txt"))]
        return max(fs, key=os.path.getmtime) if fs else None

    def run(self):
        while not self.stop_flag:
            f = self.newest()
            if f:
                size = os.path.getsize(f)
                start = self.pos.get(f, size)  # لا نقرأ التاريخ القديم
                if size > start:
                    with open(f, "r", errors="ignore") as fh:
                        fh.seek(start)
                        for line in fh.read().splitlines():
                            hits = analyze_log_line(line)
                            if hits:
                                self.cb(line.strip(), hits)
                self.pos[f] = size
            time.sleep(1)

# ---------------------------------------------------------------- وضع الاكتشاف
SKIP_PARTS = ("\\windows\\", "\\microsoft\\", "\\google\\", "\\mozilla\\", "\\node_modules\\", "\\.git\\", "\\crashpad\\")

def discovery_roots():
    e = os.environ.get
    roots = [e("APPDATA"), e("LOCALAPPDATA"), e("TEMP"), e("ProgramData"),
             os.path.join(e("ProgramFiles(x86)", ""), "Aisi"), os.path.join(e("ProgramFiles", ""), "Aisi"),
             os.path.join(e("ProgramFiles(x86)", ""), "3uTools"), os.path.join(e("ProgramFiles", ""), "3uTools"),
             os.path.join(os.path.expanduser("~"), "Documents"), "C:\\3uTools", "D:\\3uTools", "C:\\3uToolsV9", "D:\\3uToolsV9"]
    out = []
    for r in roots:
        if r and os.path.isdir(r) and r not in out:
            out.append(r)
    return out

def snapshot(roots, limit=400000):
    snap, n = {}, 0
    for r in roots:
        for dp, dn, fn in os.walk(r):
            low = dp.lower() + "\\"
            if any(s in low for s in SKIP_PARTS):
                dn[:] = []
                continue
            for f in fn:
                p = os.path.join(dp, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                snap[p] = (st.st_mtime, st.st_size)
                n += 1
                if n >= limit:
                    return snap
    return snap

def diff_snapshots(before, after):
    """الملفات الجديدة أو اللي كبر حجمها/تعدّلت أثناء الفلاش، الأكثر نمواً أولاً."""
    res = []
    for p, (mt, sz) in after.items():
        old = before.get(p)
        if old is None:
            res.append((p, sz, "جديد"))
        elif mt > old[0] and sz != old[1]:
            res.append((p, sz - old[1], "تغيّر"))
    return sorted(res, key=lambda x: -abs(x[1]))

def looks_textual(path, n=2048):
    try:
        with open(path, "rb") as fh:
            b = fh.read(n)
        return bool(b) and b.count(b"\x00") == 0
    except OSError:
        return False

# ================================================================ Nawaf Team — الخادم المحلي
import http.server, urllib.parse, secrets, plistlib, webbrowser, platform

APP, VER, BUILD = "Nawaf Team", "1.8", "10.11.2026"
CFG_PATH = os.path.join(os.path.expanduser("~"), ".nawaf_team.json")
DEMO = {"on": False}
TOKEN = secrets.token_hex(16)
LAST_HIT = [0.0]

def _add_tools_to_path():
    """يضيف مجلدات أدوات libimobiledevice الموجودة بجانب البرنامج إلى PATH، حتى يلقاها البرنامج بدون تثبيت."""
    here = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    add = []
    for d in (here, os.path.join(here, "libimobiledevice"), os.path.join(here, "tools"), os.path.join(here, "tools", "libimobiledevice")):
        for ext in ("", ".exe"):
            if os.path.isfile(os.path.join(d, "idevice_id" + ext)):
                add.append(d)
                break
    if add:
        os.environ["PATH"] = os.pathsep.join(add + [os.environ.get("PATH", "")])
_add_tools_to_path()

def cfg_load():
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def cfg_save(d):
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

# ---------------------------------------------------------------- تحليل أعمق للأخطاء
CAT_POSS = {}
CAT_CHECKS = {}
FAULT_EXTRA = {}
STAGES = [
    (r"Enter(?:ing)? Recovery", "Entering Recovery Mode"),
    (r"Extract(?:ing)? (?:BuildManifest|Restore\.plist)|Checking iDevice firmware|Reading NONCE|Requesting SHSH", "Preparing firmware / SHSH"),
    (r"bootchain", "Writing bootchain"),
    (r"iBSS|iBEC|iBoot|LLB", "Sending bootloader"),
    (r"flashing|restoring system|writing system|writing|extracting|verifying", "Writing / Restoring System"),
    (r"checking|detect", "Checking device"),
    (r"start", "Starting restore"),
]
CODE_RX = re.compile(r"(?:error|err(?:or)?\s*code|code)[^\d\-]{0,20}(-?\d{1,5})", re.I)

ASR_RX = re.compile(r"\bASR\b|filesystem payload|write Filesystem", re.I)
RESTORE_FAIL_RX = re.compile(r"restore (?:iDevice|device)\s*\((-?\d+)\)", re.I)

BB_RX = re.compile(r"baseban|didn.?t accept BasebandData|baseband[^\n]{0,40}(?:fail|error|unable)|unable[^\n]{0,30}baseband", re.I)

BB_SRC = "https://discussions.apple.com/thread/6459968"

def build_bb_analysis(line):
    if re.search(r"didn.?t accept BasebandData", line, re.I):
        cause = "الجهاز رفض بيانات الباسباند (BasebandData) أثناء تحديث المودم. هذا أول خطأ ظهر في السجل. التصنيف تقديري."
    else:
        cause = ("ظهر في سجل الفلاش سطر متعلق بمرحلة الباسباند (تحديث المودم). السطر كما ورد حرفياً أدناه، ولا يوجد له تفسير موثّق من الجهاز نفسه. "
                 "هذه الأسطر مذكورة بسجل استعادة حقيقي لجهاز قديم عبر iTunes (%s) وصيغة 3uTools قد تختلف؛ "
                 "وبحسب ذلك النقاش قد يكون الخطأ 1 من الأخطاء التي تشير لمشكلة عتاد تمنع التحديث، دون تحديد القطعة. "
                 "حتى لو أكمل الفلاش بعدها، فهذا يعني أن مرحلة الباسباند لم تُكتب بنجاح." % BB_SRC)
    return {"code": "BasebandData", "stage": "Updating Baseband", "cat": "BB", "cat_ar": CAT_AR["BB"],
            "cause": cause,
            "poss": [],
            "checks": [],
            "line": line}

def build_asr_analysis(code, line):
    return {"code": code, "stage": "Writing filesystem (ASR)", "cat": "USB", "cat_ar": CAT_AR["USB"] + " / " + CAT_AR["MEM"],
            "cause": "تعذّر إرسال ملف النظام إلى الجهاز أثناء الاستعادة (ASR): البيانات ما وصلت للجهاز (0 من 131072 بايت). التصنيف تقديري.",
            "poss": [],
            "checks": [],
            "line": line}

FAIL_RX = re.compile(r"Checkpoint\s+FAILURE\s+id:\s*(0x[0-9A-Fa-f]+)\s+result\s*=\s*(-?\d+)\s*:?\s*(.*)", re.I)
WARN_RX = re.compile(r"Checkpoint\s+WARNING\s+id:\s*(0x[0-9A-Fa-f]+)\s+result\s*=\s*-?\d+\s*:?\s*(.*)", re.I)
CKPT_RX = re.compile(r"(?:Checkpoint\s+(?P<k1>started|completed)|(?P<k2>Started|Completed)\s+checkpoint)\s+id:\s*(?P<id>0x[0-9A-Fa-f]+)\s*\((?P<name>[\w.\-]+)\)(?:\s*result\s*=\s*(?P<res>-?\d+))?", re.I)
CKPT_CAT = [(r"nand|storage|partition|nvme", "MEM"), (r"baseband|modem", "BB"), (r"fdr|image|verify|signature|ticket|tss|ipsw", "SW")]

def build_ckpt_analysis(name, cid, res, line):
    cat = next((c for rx, c in CKPT_CAT if re.search(rx, name, re.I)), None)
    poss = CAT_POSS.get(cat) or []
    checks = CAT_CHECKS.get(cat) or []
    return {"code": res, "stage": f"{name} ({cid})", "cat": cat, "cat_ar": CAT_AR.get(cat, "غير مصنّف"),
            "cause": f"سجّل 3uTools فشل المرحلة {name} ({cid}) بنتيجة {res}. السجل لا يذكر السبب، ولا يوجد تفسير موثّق لهذه المرحلة في البرنامج.",
            "poss": poss, "checks": checks, "line": line}

def build_analysis(code, stage, line, hits=None):
    cat, cause = (None, "")
    if code in RESTORE_CODES:
        cat, cause = RESTORE_CODES[code]
    elif hits:
        cat = hits[0][0]; cause = hits[0][1]
    else:
        cause = "الكود غير موجود في قاعدة البيانات الحالية — راجع السطر الأصلي."
    ex = FAULT_EXTRA.get(code, {})
    return {
        "code": code or "—", "stage": stage or "—", "cat": cat, "cat_ar": CAT_AR.get(cat, "غير مصنّف"),
        "cause": ex.get("cause", cause),
        "poss": ex.get("poss") or CAT_POSS.get(cat, []),
        "checks": ex.get("checks") or CAT_CHECKS.get(cat, []),
        "line": line,
    }

# ---------------------------------------------------------------- مراقب الفلاش
PROG_RX = re.compile(r"(?:flashing|erase[^\n]{0,20}flashing)?[^\n]{0,15}in\s*progress[^\d\n]{0,6}(\d{1,3})\s*%", re.I)

# مرجع فلاش سليم (من لوغ 3uTools حقيقي لجهاز سليم): اسم المرحلة -> [بداية بالثواني من أول مرحلة، مدة بالثواني]
REF_FLASH = json.loads('{"iPhone18,3":{"name":"iPhone 17 (healthy flash)","total":"12 min","src":"healthy flash log 13.11.2026","stages":{"is_host_compatible":[0.0,0.0],"set_weight_from_options":[0.0,0.0],"libauthinstall_callback":[0.0,0.0],"set_device_time":[0.0,0.0],"device_has_hoover":[0.0,0.0],"update_ticket":[0.0,0.0],"wait_for_storage":[0.0,0.0],"update_NAND_firmware":[0.0,5.8],"clear_remap_variable":[5.8,0.0],"print_nvram_variables":[5.8,0.0],"asp_nand_set_writable":[5.8,0.0],"find_filesystem_partitions":[5.8,0.0],"verify_storage_for_update":[5.8,0.0],"load_sep_os":[5.8,3.1],"preload_keys_for_fsck":[8.8,0.0],"fsck_filesystems":[8.8,0.1],"mount_system_partition":[8.9,0.2],"fix_cache_delete":[9.0,0.0],"delete_mobilebackups":[9.0,0.0],"clean_update_volume":[9.0,0.0],"space_checks":[9.0,0.0],"clear_mobile_gestalt_cache":[9.0,0.0],"read_previous_os_build_version":[9.0,0.0],"downgrade_check":[9.0,0.0],"copy_hardware_info":[9.0,0.0],"fdr_create":[9.0,0.1],"fdr_save_data":[9.1,0.1],"store_baseband_migration":[9.2,0.0],"check_for_restore_log":[9.2,0.0],"check_for_ota_logs":[9.2,0.0],"submit_update_stats":[9.2,0.0],"read_persistent_files":[9.2,0.2],"unmount_unconditionally":[9.3,0.0],"clean_nand":[9.3,0.4],"format_effaceable_storage":[9.7,0.0],"format_ean_storage":[9.7,0.0],"create_filesystem_partitions":[9.7,0.1],"perform_main_os_prepare":[9.7,1.8],"update_partitions_for_apfs":[9.7,0.0],"reload_filesystem_partitions":[9.7,0.0],"delete_recovery_partition":[9.7,0.0],"format_media":[9.7,0.7],"find_volumes_after_format":[10.4,0.0],"prepare_for_system_image":[10.4,0.3],"baseband_migration_data":[10.7,0.0],"init_gigalocker":[10.7,0.3],"init_permanent_update_volume":[10.9,0.2],"mount_filesystem":[11.1,0.4],"restore_system_image":[11.5,0.0],"write_persistent_files":[11.5,0.0],"perform_short_prepare":[11.5,0.0],"perform_restore_installing":[11.5,569.9],"fdr_restore_saved_data":[11.5,0.0],"update_device_firmware_pre_fdr":[11.5,0.0],"update_aht":[11.5,59.0],"fdr_prepare":[11.5,0.1],"clear_fips_data_file":[11.6,0.0],"fdr_recover":[11.6,0.0],"fdr_auto_challenge_claim":[11.6,0.0],"update_device_firmware":[11.6,136.1],"update_veridian":[11.6,0.0],"update_manta_mcu":[11.6,0.0],"update_iBoot":[11.6,2.8],"update_ean":[14.4,0.7],"install_fud":[15.1,55.1],"update_stockholm":[70.2,0.0],"update_baseband_legacy":[70.2,0.0],"update_baseband":[70.2,0.0],"update_rose":[70.2,0.0],"update_se":[70.2,0.0],"update_vinyl":[70.2,0.0],"update_ir_mcu":[70.2,0.0],"update_tcon":[70.5,0.0],"update_appletcon":[70.5,0.0],"update_orion":[70.5,0.0],"update_madea":[70.5,0.0],"update_peppy":[70.5,0.0],"update_nitrogen":[70.5,0.0],"update_usbc":[70.5,0.0],"update_centauri":[70.5,6.3],"update_canary":[76.8,0.0],"update_savage":[76.8,5.3],"await_update_tcon":[82.1,0.0],"await_update_appletcon":[82.1,0.0],"await_update_orion":[82.1,0.0],"await_update_peppy":[82.1,0.0],"await_update_nitrogen":[82.1,0.0],"await_update_usbc":[82.1,0.0],"update_ace3":[82.1,0.0],"update_usbcretimer":[82.1,0.0],"await_update_manta_mcu":[82.1,0.0],"await_update_stockholm":[82.1,0.0],"await_update_baseband_legacy":[82.1,58.0],"await_update_baseband":[140.1,0.0],"await_update_rose":[140.1,0.0],"await_update_se":[140.1,4.3],"await_update_vinyl":[144.4,3.4],"await_update_update_veridian":[147.8,0.0],"update_appletconnuarp":[147.8,0.0],"update_banyan":[147.8,0.0],"verify_fdr_data":[147.8,0.1],"update_firmware_post_sealing":[147.9,9.7],"update_savage_postseal":[147.9,0.0],"decompress_pearl_data":[147.9,6.3],"update_rose_postseal":[154.1,3.3],"stage_fdr_ean":[157.5,0.0],"ensure_fdr_ean":[157.5,0.1],"update_fdr_ean":[157.5,0.1],"update_fillmore":[157.6,0.0],"baseband_postseal":[157.6,0.0],"fdr_verify_sealed_manifest":[157.6,0.0],"commit_sep_os":[157.6,3.3],"await_system_image_invert_retry":[160.9,247.6],"create_protected_filesystems":[408.4,5.3],"create_production_dcrt":[413.7,61.5],"reserve_overprov_space":[475.2,0.0],"read_new_os_build_version":[475.2,0.0],"install_splat":[475.2,100.9],"install_kernel_cache":[576.1,1.1],"install_device_tree":[577.1,0.0],"install_root_hash":[577.1,0.1],"write_ticket":[577.1,0.0],"check_mounted":[577.1,4.2],"fixup_var":[577.1,4.2],"restore_ota_logs":[581.3,0.0],"create_partition_mountpoints":[581.3,0.0],"attest_restore":[581.3,0.0],"set_one_time_boot_command":[581.3,0.0],"set_nvram_variables":[581.3,0.0],"persistent_boot_args":[581.3,0.0],"set_tdm":[581.3,0.0],"populate_preboot_volume":[581.3,0.1],"recovery_os_restore":[581.4,0.0],"set_boot_command":[581.4,0.0],"seal_system_volume":[581.4,7.1],"create_system_snapshot":[588.5,0.2],"prepare_data_split":[588.7,0.0],"convert_eds_volumes":[588.7,0.0],"split_data_volume":[588.7,0.0],"commit_temporary_checkpoint":[588.7,0.0],"migrate_user_fsevents":[588.7,0.4],"write_os_info_for_nerd":[589.1,0.0],"set_autoboot_delay":[589.1,0.0],"unmount_filesystems":[589.1,0.1],"cleanup_boot_command":[589.1,0.0],"cleanup_recovery_os_volume":[589.1,0.0],"cleanup_check_result":[589.1,0.0],"cleanup_run_leaks":[589.1,0.0],"cleanup_send_crash_logs":[589.1,0.0],"cleanup_send_final_status":[589.1,null]}}}')
COND_RX = [("mode", re.compile(r"Found a device with mode:\s*(\S+)", re.I)),
           ("engine", re.compile(r"(Running iTunes flashing program)", re.I)),
           ("fw", re.compile(r"Firmware version:\s*(\S+)", re.I)),
           ("build", re.compile(r"Product Build:\s*(\S+)", re.I)),
           ("variant", re.compile(r"(Customer (?:Erase|Upgrade|Update)[A-Za-z ]*Install)", re.I)),
           ("behavior", re.compile(r"requested restore behavior:\s*(\w+)", re.I))]
PROD_RX = re.compile(r"iPhone\d+,\d+")
SUCCESS_RX = re.compile(r"Congratulations!?\s*Flashing completed successfully|Finished RestoreOS Restore Phase:\s*Successful|<Restore Device [^>]*>:\s*Restore succeeded", re.I)
TOOK_RX = re.compile(r"\(Time:\s*([^)]+)\)|Time used:\s*(.+)", re.I)

# أسطر ظهرت كلها في سجل فلاش ناجح حقيقي (iPhone 17، 3uTools)، فلا تُلوَّن كأخطاء ولا تُحتسب خطأً مكتشفاً.
# أسطر الباسباند (No baseband chipid / Options.plist / bbfw item) لم تُدرج هنا عمداً.
NOISE_RX = re.compile("|".join([
    r"AMDeviceIoControl:\s*(?:GetOverlappedResult failed|failed,\s*error\s*\d+,\s*usbd status)",
    r"USBControlTransfer:\s*error\s*\d+,\s*usbd status",
    r"command device request for '(?:getenv [\w\-]+|set_spin_on_panic[^']*)' failed:\s*\d+",
    r"Failed to copy env variable, error:\d+",
    r"radio-error not set",
    r"AMAuthInstallApImg\d+(?:FindProperty: failed to find property tag|CopyPayloadVersionProperty: failed to parse version property)",
    r"unable to open device_map\.txt: No such file or directory",
    r"WinAMRestore::StartRestoreLog: GetModuleHandleA failed",
    r"recovery mode device can.?t get board config with NULL device map",
    r"failed to get board config from recovery mode device",
    r"Failed to extract UniqueChipID from booted mode device|failed to get ECID from booted mode device|Failed to get ECID for booted OS device",
    r"_copyDeviceProperty\(\) failed for (?:restore bootargs|mpn)",
    r"Can.?t return padding information since it.?s not in the Restore\.plist",
    r"\[PurpleReverseProxy\].*<Error>:\s*(?:socks\d+server\.c|reverseproxyhost\.c|rpsocket\.cpp)",
]), re.I)

ASRP_RX = re.compile(r"(Sending|Verifying) file system via ASR (\d+)%", re.I)

def ck_cat(name):
    n = name.lower()
    if re.search(r"baseband|modem", n): return "BB"
    if re.search(r"nand|storage|partition|filesystem|format|media|effaceable|asp_|fsck|mount|volume|overprov|snapshot|seal|persistent|gigalocker|dcrt", n): return "MEM"
    if re.search(r"^update_|install_fud|await_update|firmware|iboot", n): return "FW"
    if re.search(r"kernel|device_tree|root_hash|ticket|nvram|boot|preboot|recovery|tdm|cleanup|attest", n): return "SYS"
    return "OTH"

def _ts(t):
    try:
        h, m, sec = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)
    except Exception:
        return None

def _dur(a, b):
    x, y = _ts(a or ""), _ts(b or "")
    if x is None or y is None:
        return None
    d = y - x
    return d + 86400 if d < 0 else d

FLASH_FAIL_NOTES = [
    (r"T200: Get Firmware Image|update_veridian|ChemID",
     "فشل تحديث فيرموير البطارية (Veridian): البطارية غير أصلية غالباً. ما بيأثر على إكمال الفلاش. جرّب بطارية أصلية إذا بدك يروح الخطأ.",
     "مجرّبة على iPhone 15 Pro Max ببطارية غير أصلية (18.11.2026)."),
    (r"cmdPerformNextStage|failed to perform next stage",
     "هالفشل بمرحلة البيسباند طلع على أجهزة البيسباند فيها سليم: تأكد إذا المودم فيرموير ظاهر بعد الفلاش قبل ما تحكم على البيسباند.",
     "مجرّبة على iPhone 13 Pro سليم وiPhone 15 Pro Max (المودم فيرموير ظاهر بعد الفلاش، 18.11.2026)."),
]

def _fail_note(f):
    txt = "%s %s %s" % (f.get("id", ""), f.get("name", ""), f.get("detail", ""))
    for rx, note, src in FLASH_FAIL_NOTES:
        if re.search(rx, txt, re.I):
            return {"note": note, "src": src}
    return None

class Monitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()
        self.watcher = None

    def reset(self):
        with self.lock:
            self.lines, self.progress, self.stage, self.analysis = [], 0, "", None
            self.dropped = 0
            self.asr = False
            self.root = None
            self.logpath = None
            self.ck = {}
            self.fails, self.warns, self._lastfail = {}, [], None
            self.bb = {"rejects": 0, "updating": 0, "sending": 0, "version": ""}
            self.success, self.took = False, ""
            self.tl, self._tail, self.last_action, self.asrsteps = {}, [], None, []
            self.failed = None
            self.product = ""
            self.cond = {}
            self.end_reached = False
            self.running, self.source = False, ""
            self._last_feed, self._refed = time.monotonic(), False

    def _trim(self):
        # نحتفظ بآخر 500 سطر، ونعدّ ما حُذف حتى يبقى ترقيم الأسطر مطلقاً (يمنع تجمّد الواجهة بعد 500 سطر)
        if len(self.lines) > 500:
            self.dropped += len(self.lines) - 500
            self.lines = self.lines[-500:]

    def _save(self, msg):
        try:
            if not self.logpath:
                d = sl_dir()
                os.makedirs(d, exist_ok=True)
                self.logpath = os.path.join(d, "flash_" + time.strftime("%Y%m%d_%H%M%S") + ".log")
            with open(self.logpath, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    def feed(self, raw):
        msg = raw.strip()
        if not msg:
            return
        self._last_feed = time.monotonic()
        msg = re.sub(r"(result=)[OoQ]\b", r"\g<1>0", msg)   # OCR يقرأ الصفر حرف O
        pm = PROG_RX.search(msg)
        if pm:    # سطر النسبة المتغيّر (Flashing in progress NN%): يحدّث الشريط فقط ولا يُضاف للسجل
            v = int(pm.group(1))
            if v <= 100:
                with self.lock:
                    self.progress = v
            return
        self._save(msg)
        tk = TOOK_RX.search(msg)
        if tk:
            with self.lock:
                self.took = (tk.group(1) or tk.group(2) or "").strip()[:60]
        if SUCCESS_RX.search(msg):
            with self.lock:
                self.success = True
                self.progress = 100
                self.stage = "اكتمل الفلاش بنجاح"
                if self.analysis and self.analysis.get("generic"):
                    self.analysis = None
                if self.root and self.root.get("generic"):
                    self.root = None
                self.lines.append({"t": datetime.now().strftime("%H:%M:%S"), "tag": "Restore", "msg": msg[:300]})
                self._trim()
            return
        tmx = re.match(r"(\d{1,2}:\d{2}:\d{2}(?:\.\d+)?):\s", msg)
        logt = tmx.group(1) if tmx else ""
        with self.lock:
            for _k, _rx in COND_RX:
                if _k not in self.cond:
                    _cm = _rx.search(msg)
                    if _cm:
                        self.cond[_k] = (_cm.group(1) if _cm.groups() else _cm.group(0)).strip()[:80]
            if not self.product:
                _pm = PROD_RX.search(msg)
                if _pm:
                    self.product = _pm.group(0)
            self._tail.append(msg[:220])
            self._tail = self._tail[-14:]
            if re.match(r"\d{1,2}:\d{2}:\d{2}(?:\.\d+)?:\s{2,}\S", msg):
                _txt = re.sub(r"^\S+:\s+", "", msg)[:160]
                if re.match(r"ERROR\b|Flash(?:ing)? Failed", _txt, re.I) and not self.success:
                    if not self.failed:
                        self.failed = {"t": logt, "text": _txt, "after": (self.last_action or {}).get("text", ""), "after_t": (self.last_action or {}).get("t", "")}
                        self.stage = "توقف عند: " + (self.last_action or {}).get("text", "")[:60]
                else:
                    self.last_action = {"t": logt, "text": _txt}
                    if re.match(r"Sending NORData", _txt, re.I):
                        _op = [e["name"] for e in self.tl.values() if e["end"] is None and not e["id"].startswith("nor")]
                        self.tl["nor%d" % len(self.tl)] = {"id": "nor", "name": "Sending NORData" + (" (in " + _op[-1] + ")" if _op else ""), "cat": "MEM", "start": logt, "end": logt}
                    if re.match(r"Cleaning up", _txt, re.I) and any(e["name"] == "cleanup_send_final_status" for e in self.tl.values()):
                        self.end_reached = True    # آخر سطر قبل شريط النجاح بلوغ 3uTools (شريط النجاح خارج نص اللوغ)
                        if not self.success:
                            self.progress = 100
                            self.stage = "وصل لآخر مرحلة (Cleaning up)"
                            if self.analysis and self.analysis.get("generic"):
                                self.analysis = None
                            if self.root and self.root.get("generic"):
                                self.root = None
            am = ASRP_RX.search(msg)
            if am and (not self.asrsteps or (self.asrsteps[-1][0], self.asrsteps[-1][1]) != (am.group(1).lower(), int(am.group(2)))):
                self.asrsteps.append((am.group(1).lower(), int(am.group(2)), logt))
        if self._lastfail and not re.match(r"\d{1,2}:\d{2}:\d{2}", msg):    # سطر تكملة لتفاصيل FAILURE (يبدأ بلا وقت)
            with self.lock:
                f = self.fails.get(self._lastfail)
                if f:
                    f["detail"] = (f["detail"] + " " + msg)[:600]
                self.lines.append({"t": datetime.now().strftime("%H:%M:%S"), "tag": "Error", "msg": msg[:300]})
                self._trim()
            return
        self._lastfail = None
        fm0 = FAIL_RX.search(msg)
        if fm0:
            with self.lock:
                cid = fm0.group(1).upper().replace("0X", "0x")
                self.fails[cid] = {"id": cid, "res": fm0.group(2), "detail": fm0.group(3).strip()[:600],
                                   "name": (self.ck.get(cid) or {}).get("name", ""), "t": msg[:12]}
                self._lastfail = cid
        wm0 = WARN_RX.search(msg)
        if wm0:
            with self.lock:
                self.warns.append({"id": wm0.group(1), "detail": wm0.group(2).strip()[:300], "t": msg[:12]})
        if ASR_RX.search(msg):
            self.asr = True
        with self.lock:
            if re.search(r"didn.?t accept BasebandData", msg, re.I):
                self.bb["rejects"] += 1
            elif re.search(r"\bUpdating Baseband\b", msg, re.I):
                self.bb["updating"] += 1
            elif re.search(r"\bSending BasebandData\b", msg, re.I):
                self.bb["sending"] += 1
            mv = re.search(r"Baseband Version[^\n]*", msg, re.I)
            if mv:
                self.bb["version"] = mv.group(0)[:120]
        ck = CKPT_RX.search(msg)
        if ck:
            kind, cid, name, res = (ck.group("k1") or ck.group("k2")).lower(), ck.group("id"), ck.group("name"), ck.group("res")
            with self.lock:
                e = self.ck.setdefault(cid, {"id": cid, "name": name, "res": None, "starts": 0, "done": False})
                if kind == "started":
                    e["starts"] += 1
                else:
                    e["done"] = True
                    e["res"] = res
            with self.lock:
                t_ = self.tl.get(cid)
                if t_ is None:
                    t_ = self.tl[cid] = {"id": cid, "name": name, "cat": ck_cat(name), "start": None, "end": None}
                if kind == "started":
                    if t_["start"] is None:
                        t_["start"] = logt
                else:
                    t_["end"] = logt
                    if t_["start"] is None:
                        t_["start"] = logt
            bad = kind == "completed" and res not in (None, "0")
            with self.lock:
                self.lines.append({"t": datetime.now().strftime("%H:%M:%S"), "tag": "Error" if bad else "Restore", "msg": msg[:300]})
                self._trim()
                self.stage = f"{name} ({cid})"
                if bad:
                    self.analysis = build_ckpt_analysis(name, cid, res, msg)
                    self.analysis["generic"] = True    # فشل مرحلة داخلية: يُمسح إذا اكتمل الفلاش (لوغ حقيقي لجهاز سليم يحوي مثل هذه الفشول)
            return
        if NOISE_RX.search(msg):
            tag = "3uTools"
        elif re.search(r"error|failed|fail\b|unable to|didn.?t accept|cannot|can.?t |BasebandHandleUpdaterStatus[^\n]*(?:commandAccepted: NO|outputDict is NULL)|No baseband chipid", msg, re.I):
            tag = "Error"
        elif re.search(r"restore|iBSS|iBEC|bootchain|flashing|checking|sending|writing|DFU|ECID", msg, re.I):
            tag = "Restore"
        else:
            tag = "3uTools"
        if tag == "Error" and self.success and not BB_RX.search(msg):
            tag = "3uTools"    # بعد نجاح الفلاش، أسطر <Error> العابرة ليست فشلاً
        with self.lock:
            self.lines.append({"t": datetime.now().strftime("%H:%M:%S"), "tag": tag, "msg": msg[:300]})
            self._trim()
            m = re.search(r"(\d{1,3})\s*%", msg)
            if m and int(m.group(1)) <= 100 and not self.success:
                self.progress = int(m.group(1))
            if tag != "Error" and not self.success:
                for rx, name in STAGES:
                    if re.search(rx, msg, re.I):
                        self.stage = name
                        break
            else:
                fm = RESTORE_FAIL_RX.search(msg)
                new = None
                if BB_RX.search(msg):
                    new = build_bb_analysis(msg)
                    if not re.search(r"didn.?t accept BasebandData", msg, re.I):
                        new["generic"] = True    # أسطر باسباند عامة تظهر بفلاش ناجح أيضاً (مثبت من سجل حقيقي)
                elif self.asr and (fm or ASR_RX.search(msg)):
                    prev = (self.analysis or {}).get("code") if (self.analysis or {}).get("stage", "").endswith("(ASR)") else None
                    new = build_asr_analysis(fm.group(1) if fm else (prev or "ASR"), msg)
                else:
                    cm = CODE_RX.search(msg)
                    code = cm.group(1).lstrip("-") if cm else ""
                    hits = analyze_log_line(msg)
                    if (code or hits) and not self.success and not self.end_reached:
                        new = build_analysis(code, self.stage, msg, hits)
                        new["generic"] = True
                if new:
                    if self.root is None:
                        self.root = new
                    if self.root.get("cat") == "BB" and new.get("stage", "").endswith("(ASR)"):
                        a = dict(self.root)    # أول خطأ هو السبب الجذري؛ الأخطاء اللاحقة نتيجة له غالباً
                        if new["code"] != "ASR":
                            a["code"] = new["code"]
                        a["cause"] = self.root["cause"] + f" تبعه لاحقاً فشل إرسال ملف النظام (ASR) ورمز الفشل الأخير {new['code']}، وهو غالباً نتيجة للخطأ الأول."
                        self.analysis = a
                    else:
                        self.analysis = new

    def _final_failed(self):
        """خطأ ERROR يُعرض كفشل نهائي فقط إذا لم تبدأ أي مرحلة بعده (وإلا فالفلاش تابع بعد الخطأ)."""
        f = self.failed
        if not f or self.success:
            return None
        if any(e["start"] > f["t"] for e in self.tl.values()):
            return None
        return f

    def snapshot(self, since):
        with self.lock:
            total = self.dropped + len(self.lines)
            return {"total": total, "lines": self.lines[max(0, since - self.dropped):] if since <= total else self.lines,
                    "reset": since > total, "progress": self.progress, "stage": self.stage,
                    "running": self.running, "source": self.source, "analysis": self.analysis,
                    "stages": list(self.ck.values()), "bb": dict(self.bb),
                    "fails": [dict(f, name=f["name"] or (self.ck.get(f["id"]) or {}).get("name", ""), note=_fail_note(dict(f, name=f["name"] or (self.ck.get(f["id"]) or {}).get("name", "")))) for f in self.fails.values()],
                    "warns": list(self.warns), "savedto": self.logpath or "",
                    "success": self.success, "took": self.took,
                    "timeline": [dict(e, dur=_dur(e["start"], e["end"])) for e in self.tl.values()],
                    "asrsteps": self._asr_view(),
                    "last_action": self.last_action, "failed": self._final_failed(), "product": self.product, "end_reached": self.end_reached and not self.success, "cond": dict(self.cond), "ref": refs_flash_get(self.product),
                    "open": [dict(e) for e in self.tl.values() if e["end"] is None][-6:],
                    "hw": self._hw_view(),
                    "tail": list(self._tail[-10:])}

    def _hw_view(self):
        """حالات المستخدم للفلاش: الفلاش متوقف (لا أسطر جديدة 60 ث، أو سجل محمّل من ملف) وآخر مرحلة بدأت ولم تنتهِ اسمها مطابق للحالة."""
        try:
            if self.success or not self.tl:
                return []
            op = [e for e in self.tl.values() if e["end"] is None]
            if not op:
                return []
            if not (self._refed or time.monotonic() - self._last_feed >= 60):
                return []
            last = op[-1]["name"]
            return [{"net": c["net"], "model": c["model"], "title": "", "stage": last, "fix": c.get("fix", "")}
                    for c in HW_CASES if c.get("match_flash_open") and c["match_flash_open"].lower() == str(last).lower()]
        except Exception:
            return []

    def _asr_view(self):
        out, prev = [], None
        for k, pct, t in self.asrsteps:
            if k == "sending" and pct % 10 == 0:    # نجمّع كل 10% حتى لا تظهر توقفات صغيرة كإنذار
                out.append({"pct": pct, "t": t, "d": _dur(prev, t) if prev else None})
                prev = t
        ds = sorted(x["d"] for x in out if x["d"] is not None and x["d"] > 0)
        med = ds[len(ds) // 2] if ds else None
        for x in out:
            x["slow"] = bool(med and x["d"] is not None and x["d"] > 2 * med)
        return out

    def start(self, folder):
        self.stop()
        self.reset()
        self.watcher = Tail(folder, self.feed)
        self.watcher.start()
        with self.lock:
            self.running, self.source = True, folder

    def refeed(self, lines, source):
        self.reset()
        for ln in lines:
            self.feed(ln)
        with self.lock:
            self.running, self.source = True, source
            self._refed = True

    def start_win(self):
        self.stop()
        self.reset()
        self.watcher = WinWatch(self)
        self.watcher.start()
        with self.lock:
            self.running, self.source = True, "نافذة 3uTools"

    def stop(self):
        if self.watcher:
            self.watcher.stop_flag = True
            self.watcher = None
        with self.lock:
            self.running = False

# ---- قراءة نافذة 3uTools مباشرة (Windows UI Automation عبر PowerShell)
UIA_PS = r"""
[Console]::OutputEncoding=[Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$ids=@(Get-Process | Where-Object { $_.Name -match '3u' } | ForEach-Object { $_.Id })
$root=[System.Windows.Automation.AutomationElement]::RootElement
$seen=@{}
$isadm=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Output ("##DIAG nawaf_admin=" + $isadm + " pids=" + ($ids -join ","))
$nw=0;$nd=0
foreach($id in $ids){
  $c=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$id)
  foreach($w in $root.FindAll([System.Windows.Automation.TreeScope]::Children,$c)){
    $nw++
    foreach($e in $w.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)){
      $nd++
      $vals=@()
      try{ $vals+=$e.Current.Name }catch{}
      try{ $p=$null; if($e.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$p)){ $vals+=$p.Current.Value } }catch{}
      try{ $p=$null; if($e.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern,[ref]$p)){ $vals+=$p.DocumentRange.GetText(200000) } }catch{}
      foreach($v in $vals){ if($v -and $v.Length -gt 3 -and -not $seen.ContainsKey($v)){ $seen[$v]=1; Write-Output $v } }
    }
  }
}
Write-Output ("##DIAG windows=" + $nw + " elements=" + $nd)
"""
LOGLINE_RX = re.compile(r"\d{2}:\d{2}:\d{2}|error|fail|restore|checkpoint|baseband|flash|ASR\b", re.I)

OCR_PS = r'''
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$ErrorActionPreference='Stop'
try{
Add-Type -AssemblyName System.Runtime.WindowsRuntime,System.Drawing,UIAutomationClient,UIAutomationTypes
Add-Type @"
using System; using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential)] public struct RECTX { public int L,T,R,B; }
public class W32 {
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECTX r);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
}
"@
[void][W32]::SetProcessDPIAware()
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Storage.FileAccessMode,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
$m=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Await($op,$t){ $task=$m.MakeGenericMethod($t).Invoke($null,@($op)); [void]$task.Wait(); $task.Result }
$eng=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if(-not $eng){ Write-Output "##DIAG ocr=kein_OCR_Engine"; exit }
$ids=@(Get-Process | Where-Object { $_.Name -match '3u' } | ForEach-Object { $_.Id })
$root=[System.Windows.Automation.AutomationElement]::RootElement
$seen=@{}; $nwin=0
foreach($id in $ids){
  $c=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$id)
  foreach($w in $root.FindAll([System.Windows.Automation.TreeScope]::Children,$c)){
    $h=[IntPtr]$w.Current.NativeWindowHandle
    if($h -eq [IntPtr]::Zero){ continue }
    if([W32]::IsIconic($h)){ Write-Output "##DIAG window_minimized"; continue }
    $rc=New-Object RECTX
    [void][W32]::GetWindowRect($h,[ref]$rc)
    $wd=$rc.R-$rc.L; $ht=$rc.B-$rc.T
    if($wd -lt 200 -or $ht -lt 200){ continue }
    $nwin++
    $bmp=New-Object System.Drawing.Bitmap($wd,$ht)
    $g=[System.Drawing.Graphics]::FromImage($bmp)
    $hdc=$g.GetHdc(); [void][W32]::PrintWindow($h,$hdc,2); $g.ReleaseHdc($hdc); $g.Dispose()
    $cols=@{}
    for($yy=5;$yy -lt $ht;$yy+=[Math]::Max(1,[int]($ht/25))){ for($xx=5;$xx -lt $wd;$xx+=[Math]::Max(1,[int]($wd/25))){ $cols[$bmp.GetPixel($xx,$yy).ToArgb()]=1 } }
    $method="printwindow"
    if($cols.Count -lt 6){
      $g=[System.Drawing.Graphics]::FromImage($bmp)
      $g.CopyFromScreen($rc.L,$rc.T,0,0,(New-Object System.Drawing.Size($wd,$ht)))
      $g.Dispose(); $method="screen(PrintWindow_leer)"
    }
    Write-Output ("##DIAG img=" + $wd + "x" + $ht + " colors=" + $cols.Count + " method=" + $method)
    $big=New-Object System.Drawing.Bitmap($bmp,($wd*2),($ht*2))
    $path=Join-Path $env:TEMP ("nawaf_ocr_"+$id+"_"+$nwin+".png")
    $big.Save($path,[System.Drawing.Imaging.ImageFormat]::Png); $big.Dispose(); $bmp.Dispose()
    $file=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
    $stream=Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $dec=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $sb=Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $res=Await ($eng.RecognizeAsync($sb)) ([Windows.Media.Ocr.OcrResult])
    Write-Output ("##DIAG ocr_lines=" + @($res.Lines).Count)
    foreach($l in $res.Lines){ $t=$l.Text; if($t -and -not $seen.ContainsKey($t)){ $seen[$t]=1; Write-Output $t } }
    $stream.Dispose(); Remove-Item $path -ErrorAction SilentlyContinue
  }
}
Write-Output ("##DIAG ocr_windows=" + $nwin + " ocr_lang=" + $eng.RecognizerLanguage.LanguageTag)
}catch{ Write-Output ("##DIAG ocr_error=" + $_.Exception.Message) }
'''

OCR_ONLY = [False]

def run_ps(script, timeout):
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, timeout=timeout, creationflags=0x08000000)
    out = r.stdout.decode("utf-8", errors="ignore")
    raw, diag = [], []
    for blk in out.splitlines():
        blk = blk.strip()
        if blk.startswith("##DIAG"):
            diag.append(blk[6:].strip())
        elif blk:
            raw.append(blk)
    err = r.stderr.decode("utf-8", errors="ignore")[:300].strip()
    return raw, diag, err

def read_3utools_window():
    """يعيد (نص مفلتر، عدد الأسطر الخام، تشخيص). يجرب UI Automation ثم OCR."""
    if platform.system() != "Windows":
        return "", 0, "هذه الميزة تعمل على Windows فقط."
    info, raw = [], []
    if not OCR_ONLY[0]:
        try:
            raw, diag, err = run_ps(UIA_PS, 40)
            info.append("UIA: " + " ".join(diag) + (" stderr:" + err if err else ""))
        except Exception as e:
            info.append(f"UIA فشل: {e}")
    if len(raw) < 5:
        OCR_ONLY[0] = True
        try:
            raw, diag, err = run_ps(OCR_PS, 90)
            info.append("OCR: " + " ".join(diag) + (" stderr:" + err if err else ""))
        except Exception as e:
            raw = []
            info.append(f"OCR فشل أو انتهت المهلة: {e}")
    flat = [ln.strip() for blk in raw for ln in blk.splitlines() if ln.strip()]
    merged, i = [], 0
    while i < len(flat):      # بعض نوافذ 3uTools تُرجع الوقت في سطر والنص في السطر التالي: ندمجهما كسطر واحد
        if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}(?:\.\d+)?:?", flat[i]) and i + 1 < len(flat) and not re.match(r"\d{1,2}:\d{2}:\d{2}", flat[i + 1]):
            merged.append(flat[i].rstrip(":") + ":  " + flat[i + 1])
            i += 2
        else:
            merged.append(flat[i])
            i += 1
    lines = [ln for ln in merged if LOGLINE_RX.search(ln)]
    return "\n".join(lines), len(raw), " | ".join(info)

class WinWatch(threading.Thread):
    def __init__(self, mon):
        super().__init__(daemon=True)
        self.mon, self.stop_flag, self.seen = mon, False, set()
    def run(self):
        while not self.stop_flag:
            txt, n, err = read_3utools_window()
            # نراكم الأسطر الجديدة فقط (حتى لا يضيع أول خطأ عندما ينزل خارج النافذة)
            for ln in txt.splitlines():
                key = re.sub(r"\s+", " ", ln).strip().lower()
                if key and key not in self.seen:
                    self.seen.add(key)
                    self.mon.feed(ln)
            for _ in range(20):
                if self.stop_flag:
                    break
                time.sleep(0.1)

MON = Monitor()

def decode_bytes(b):
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16", errors="ignore")
    return b.decode("utf-8", errors="ignore")

class Tail(threading.Thread):
    """يتابع أحدث ملف .log/.txt في المجلد ويمرر كل سطر جديد."""
    def __init__(self, folder, cb):
        super().__init__(daemon=True)
        self.folder, self.cb, self.stop_flag, self.pos, self.first = folder, cb, False, {}, True

    def run(self):
        while not self.stop_flag:
            try:
                files = [f for f in glob.glob(os.path.join(self.folder, "**", "*"), recursive=True)
                         if os.path.isfile(f) and f.lower().endswith((".log", ".txt"))]
                if self.first:
                    for f in files:
                        self.pos[f] = os.path.getsize(f)
                    self.first = False
                for f in files:
                    size = os.path.getsize(f)
                    start = self.pos.get(f, 0)
                    if size < start:
                        start = 0
                    if size > start:
                        with open(f, "rb") as fh:
                            fh.seek(start)
                            data = fh.read(size - start)
                        for line in decode_bytes(data).splitlines():
                            self.cb(line)
                    self.pos[f] = size
            except Exception:
                pass
            time.sleep(1)

DEMO_SCRIPT = [
    (0.3, "Start Restore (iOS 27.2 – iPhone16,2)"),
    (1.0, "Checking device (ECID: 001A2B3C4D5E6789)"),
    (1.0, "Sending iBSS..."),
    (1.0, "Writing bootchain..."),
    (1.0, "Flashing system... 66%"),
    (1.2, "ERROR (4014)  Failed to restore device."),
]
def run_demo():
    MON.reset()
    with MON.lock:
        MON.running, MON.source = True, "DEMO"
    def go():
        for d, line in DEMO_SCRIPT:
            time.sleep(d)
            MON.feed(line)
    threading.Thread(target=go, daemon=True).start()

# ---------------------------------------------------------------- الجهاز
PRODUCT_NAMES = {
    "iPhone10,3": "iPhone X", "iPhone10,6": "iPhone X", "iPhone11,2": "iPhone XS", "iPhone11,6": "iPhone XS Max",
    "iPhone11,8": "iPhone XR", "iPhone12,1": "iPhone 11", "iPhone12,3": "iPhone 11 Pro", "iPhone12,5": "iPhone 11 Pro Max",
    "iPhone13,1": "iPhone 12 mini", "iPhone13,2": "iPhone 12", "iPhone13,3": "iPhone 12 Pro", "iPhone13,4": "iPhone 12 Pro Max",
    "iPhone14,4": "iPhone 13 mini", "iPhone14,5": "iPhone 13", "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone14,7": "iPhone 14", "iPhone14,8": "iPhone 14 Plus", "iPhone15,2": "iPhone 14 Pro", "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus", "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone8,1": "iPhone 6s", "iPhone8,2": "iPhone 6s Plus", "iPhone8,4": "iPhone SE (1st gen)",
    "iPhone9,1": "iPhone 7", "iPhone9,3": "iPhone 7", "iPhone9,2": "iPhone 7 Plus", "iPhone9,4": "iPhone 7 Plus",
    "iPhone10,1": "iPhone 8", "iPhone10,4": "iPhone 8", "iPhone10,2": "iPhone 8 Plus", "iPhone10,5": "iPhone 8 Plus",
    "iPhone12,8": "iPhone SE (2nd gen)", "iPhone14,6": "iPhone SE (3rd gen)", "iPhone17,5": "iPhone 16e",
    "iPhone18,1": "iPhone 17 Pro", "iPhone18,2": "iPhone 17 Pro Max", "iPhone18,3": "iPhone 17", "iPhone18,4": "iPhone Air",
    "iPhone17,1": "iPhone 16 Pro", "iPhone17,2": "iPhone 16 Pro Max", "iPhone17,3": "iPhone 16", "iPhone17,4": "iPhone 16 Plus",
}
DEMO_DEVICE = {
    "demo": True, "connected": True, "mode": "Normal", "name": "iPhone 15 Pro Max",
    "fields": {"Model Identifier": "iPhone16,2", "Product Type": "D84AP", "iOS Version": "27.2 (21C5023e)",
               "Serial Number": "F3L7X9A0D7", "ECID": "001A2B3C4D5E6789", "UDID": "00008120-001A2B3C4D5E6789", "Chip": "A17 Pro"},
    "right": {"color": "Natural Titanium", "capacity": "256 GB", "findmy": "مفعّل", "activation": "مفعّل", "jb": "غير موجود", "mode": "Normal Mode"},
    "battery": {"pct": 86, "cycles": 312, "health": 92}, "storage": {"used": 128.4, "total": 256},
    "modem": {"state": "ok", "title": "المودم ظاهر (Baseband و IMEI مقروءان)", "baseband": "2.60.01", "imei": "35 000000 000000 0", "bb_serial": "—", "sim": "Ready", "bb_status": "—", "poss": [], "checks": []},
}

def run(cmd, timeout=8):
    try:
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, **kw).stdout
    except Exception:
        return ""

def kv(text):
    d = {}
    for ln in text.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            d[k.strip()] = v.strip()
    return d

USB_MODES = {"1227": "DFU", "1222": "DFU", "1280": "Recovery", "1281": "Recovery", "1282": "Recovery", "1283": "Recovery"}
CPID_CHIP = {"8000": "A9", "8003": "A9", "8010": "A10", "8011": "A10X", "8015": "A11", "8020": "A12", "8027": "A12X",
             "8030": "A13", "8101": "A14", "8110": "A15", "8120": "A16", "8130": "A17 Pro", "8140": "A18"}

def parse_usb_ids(text):
    """يقرأ أسطر USB\\VID_05AC&PID_xxxx من ويندوز ويستخلص الوضع و ECID و CPID إن وجدت."""
    for ln in text.splitlines():
        m = re.search(r"VID_05AC&PID_([0-9A-F]{4})", ln, re.I)
        if not m:
            continue
        mode = USB_MODES.get(m.group(1).upper())
        if not mode:
            continue
        g = lambda rx: (re.search(rx, ln) or [None, None])[1]
        cpid = g(r"CPID:([0-9A-Fa-f]{4})")
        return {"mode": mode, "pid": m.group(1).upper(), "ecid": (g(r"ECID:([0-9A-Fa-f]{8,16})") or "—").upper(),
                "cpid": cpid, "bdid": g(r"BDID:([0-9A-Fa-f]{2})"), "srnm": g(r"SRNM:\[?([A-Za-z0-9]+)")}
    return None

# ---- ذاكرة الموديلات: نتعلم (ChipID, BoardId) و ECID كلما اتصل جهاز بوضع عادي ----
KNOWN_PATH = os.path.join(os.path.expanduser("~"), ".nawaf_devices.json")
# أزواج CPID:BDID مؤكدة من صفحات ipsw.me (والباقي يتعلّمه البرنامج من الأجهزة اللي تتصل بوضع عادي)
SEED_MODELS = {
    "8130:04": {"product": "iPhone16,1", "name": "iPhone 15 Pro", "board": "D83AP"},
    "8130:06": {"product": "iPhone16,2", "name": "iPhone 15 Pro Max", "board": "D84AP"},
}

def known_load():
    try:
        with open(KNOWN_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def known_learn(info, product):
    try:
        chip, board = int(info.get("ChipID", "")), int(info.get("BoardId", ""))
    except ValueError:
        return
    ecid = info.get("UniqueChipID", "")
    entry = {"product": product, "name": PRODUCT_NAMES.get(product, product)}
    if info.get("HardwareModel"):
        entry["board"] = info["HardwareModel"].upper()
    k = known_load()
    changed = False
    key = f"{chip:04X}:{board:02X}"
    if k.setdefault("models", {}).get(key) != entry:
        k["models"][key] = entry; changed = True
    if ecid.isdigit():
        e = format(int(ecid), "016X")
        ent = dict(entry, serial=info.get("SerialNumber", ""))
        if k.setdefault("ecids", {}).get(e) != ent:
            k["ecids"][e] = ent; changed = True
    if changed:
        try:
            with open(KNOWN_PATH, "w", encoding="utf-8") as f:
                json.dump(k, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

def known_lookup(ecid, cpid, bdid):
    k = known_load()
    if ecid and ecid != "—" and ecid.upper().lstrip("0") in {x.lstrip("0") for x in k.get("ecids", {})}:
        for e, v in k["ecids"].items():
            if e.lstrip("0") == ecid.upper().lstrip("0"):
                return v, "ecid"
    if cpid and bdid:
        key = f"{cpid.upper()}:{bdid.upper()}"
        v = k.get("models", {}).get(key) or SEED_MODELS.get(key)
        if v:
            return v, "model"
    return None, None

# ---- تحديث جدول الموديلات من api.ipsw.me (يُنفَّذ على جهازك، يحتاج إنترنت) ----
def _num(v):
    if isinstance(v, int):
        return v
    v = str(v).strip()
    return int(v, 16) if v.lower().startswith("0x") else int(v)

def parse_devices_json(data):
    models = {}
    for d in data:
        ident = str(d.get("identifier", ""))
        if not ident.startswith("iPhone"):
            continue
        try:
            cpid, bdid = _num(d.get("cpid")), _num(d.get("bdid"))
        except (TypeError, ValueError):
            continue
        models[f"{cpid:04X}:{bdid:02X}"] = {"product": ident, "name": d.get("name", ident), "board": str(d.get("boardconfig", "")).upper()}
    return models

def db_update(timeout=25):
    import urllib.request
    req = urllib.request.Request("https://api.ipsw.me/v4/devices", headers={"User-Agent": "NawafTeam/1.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        models = parse_devices_json(json.loads(r.read().decode("utf-8")))
    if not models:
        raise ValueError("الرد فارغ أو بصيغة غير متوقعة")
    k = known_load()
    cur = k.setdefault("models", {})
    for key, v in models.items():      # ما نستبدل اللي تعلّمه البرنامج من جهاز حقيقي
        if key not in cur or cur[key].get("src") == "ipsw":
            cur[key] = dict(v, src="ipsw")
    k["updated"] = int(time.time())
    with open(KNOWN_PATH, "w", encoding="utf-8") as f:
        json.dump(k, f, ensure_ascii=False, indent=1)
    return len(models)

# ---- قراءة واصفات USB مباشرة من الـ hub (ويندوز) ----
def parse_conn_info(ci):
    """USB_NODE_CONNECTION_INFORMATION_EX (packed): واصف الجهاز عند 4، IsHub عند 24، الحالة عند 31."""
    if not ci or len(ci) < 35:
        return None
    import struct
    vid, pid = struct.unpack_from("<HH", ci, 12)
    return {"vid": vid, "pid": pid, "iser": ci[20], "is_hub": bool(ci[24]), "status": struct.unpack_from("<I", ci, 31)[0]}

def parse_string_desc(ds):
    """رد IOCTL_USB_GET_DESCRIPTOR_FROM_NODE_CONNECTION: 12 بايت ترويسة ثم USB_STRING_DESCRIPTOR."""
    if not ds or len(ds) < 15:
        return None
    d = ds[12:]
    n = min(d[0], len(d))
    return d[2:n].decode("utf-16le", "ignore") if n > 2 else None

def read_usb_apple_devices(trace=None):
    if os.name != "nt":
        return []
    import ctypes, struct
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.DeviceIoControl.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k32.DeviceIoControl.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    INVALID = ctypes.c_void_p(-1).value
    found = []
    def T(m):
        if trace is not None:
            trace.append(m)
    def opn(path):
        h = k32.CreateFileW(path, 0x40000000, 2, None, 3, 0, None)
        return None if (h is None or h == INVALID) else h
    def ioctl(h, code, inbuf, outsize):
        ob = ctypes.create_string_buffer(outsize)
        ret = wintypes.DWORD(0)
        ib = ctypes.create_string_buffer(bytes(inbuf), len(inbuf)) if inbuf else None
        ok = k32.DeviceIoControl(h, code, ib, len(inbuf) if inbuf else 0, ob, outsize, ctypes.byref(ret), None)
        if not ok:
            T(f"ioctl {code:#x} failed, GetLastError={ctypes.GetLastError()}")
            return None
        return ob.raw[:ret.value]
    def walk(path, depth=0):
        h = opn(path)
        if not h:
            T(f"{'  '*depth}cannot open hub {path[:60]}")
            return
        try:
            ni = ioctl(h, 0x220408, b"", 512)
            if not ni or len(ni) < 7:
                return
            nports = ni[6]
            T(f"{'  '*depth}hub ports={nports}")
            for port in range(1, nports + 1):
                ci = parse_conn_info(ioctl(h, 0x220448, struct.pack("<I", port) + b"\0" * 508, 512))
                if not ci or ci["status"] != 1:
                    continue
                if ci["vid"] == 0x05AC:
                    serial = None
                    if ci["iser"]:
                        req = struct.pack("<IBBHHH", port, 0x80, 6, (3 << 8) | ci["iser"], 0x0409, 255) + b"\0" * 255
                        serial = parse_string_desc(ioctl(h, 0x220410, req, len(req)))
                    found.append({"pid": f"{ci['pid']:04X}", "serial": serial, "port": port})
                    T(f"{'  '*depth}APPLE port {port} pid={ci['pid']:04X} serial={serial!r}")
                if ci["is_hub"]:
                    nm = ioctl(h, 0x220414, struct.pack("<I", port) + b"\0" * 1020, 1024)
                    if nm and len(nm) > 8:
                        name = nm[8:].decode("utf-16le", "ignore").split("\x00")[0]
                        walk("\\\\.\\" + name, depth + 1)
        finally:
            k32.CloseHandle(h)
    for i in range(16):
        hc = opn("\\\\.\\HCD%d" % i)
        if not hc:
            continue
        try:
            rn = ioctl(hc, 0x220408, b"", 1024)
            if rn and len(rn) > 4:
                name = rn[4:].decode("utf-16le", "ignore").split("\x00")[0]
                T(f"HCD{i} root hub: {name[:50]}")
                walk("\\\\.\\" + name)
        finally:
            k32.CloseHandle(hc)
    return found

def usb_device_dict(r, how):
    chip = CPID_CHIP.get((r["cpid"] or "").upper())
    chip_txt = f"{chip} (CPID {r['cpid']})" if chip and r["cpid"] else (f"CPID {r['cpid']}" if r["cpid"] else "—")
    kn, via = known_lookup(r["ecid"], r["cpid"], r["bdid"])
    name = f"{kn['name']} — {r['mode']}" if kn else f"Apple device — {r['mode']}"
    note = ""
    if kn:
        note = "الموديل من جدول البرنامج حسب المعالج ورقم البورد." if via == "model" else "الموديل من جهاز سبق اتصاله بوضع عادي (نفس الجهاز بالـ ECID)."
    elif r["cpid"]:
        note = "الموديل غير معروف بعد: وصّل أي جهاز من نفس الموديل بوضع عادي مرة وسيتعلّمه البرنامج."
    return {"connected": True, "mode": r["mode"], "name": name, "note": note,
            "fields": {"Model Identifier": kn["product"] if kn else "—", "Product Type": (kn or {}).get("board") or (f"BDID {r['bdid']}" if r["bdid"] else "—"), "iOS Version": "—",
                       "Serial Number": (kn or {}).get("serial") or r["srnm"] or "—", "ECID": r["ecid"], "UDID": "—", "Chip": chip_txt},
            "right": {"color": "—", "capacity": "—", "findmy": "—", "activation": "—", "jb": "—", "mode": r["mode"] + " Mode"},
            "battery": None, "storage": None}

def usb_apple_mode(trace=None):
    if os.name != "nt":
        return None
    try:
        for d in read_usb_apple_devices(trace):
            mode = USB_MODES.get(d["pid"])
            if mode:
                r = parse_usb_ids(f"VID_05AC&PID_{d['pid']} {d['serial'] or ''}")
                return usb_device_dict(r, "hub")
    except Exception as e:
        if trace is not None:
            trace.append(f"hub reader error: {e!r}")
    ps = ("Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -like 'USB\\VID_05AC*' } | "
          "ForEach-Object { $_.PNPDeviceID + ' | ' + $_.Name }")
    r = parse_usb_ids(run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=12))
    return usb_device_dict(r, "pnp") if r else None

BB_KEY_RX = re.compile(r"baseband|imei|mobileequipment|meid|iccid|\bsim|carrier|telephony|cellular|modem|phonenumber|subscriber|radio", re.I)
BB_IDENT_RX = re.compile(r"imei|mobileequipment|meid|iccid|serial|phonenumber|subscriber|uniqueid|udid|ecid", re.I)

def bb_keys(info):
    return {k: str(v)[:120] for k, v in info.items() if BB_KEY_RX.search(k)}

def bb_profile(keys):
    return {k: ("<موجود>" if BB_IDENT_RX.search(k) else v) for k, v in keys.items()}

def bb_compare(pt, keys):
    base = (cfg_load().get("baselines") or {}).get(pt)
    if not base:
        return None
    cur = bb_profile(keys or {})
    bk = base.get("keys", {})
    return {"date": base.get("date", ""), "name": base.get("name", ""),
            "missing": [k for k in bk if k not in cur], "extra": [k for k in cur if k not in bk],
            "changed": [{"k": k, "base": bk[k], "now": cur[k]} for k in cur if k in bk and cur[k] != bk[k]][:30]}

def modem_check(info, pt):
    """فحص المودم من بيانات lockdown (وضع Normal فقط). التصنيف تقديري."""
    if not str(pt).startswith("iPhone"):
        return None
    g = lambda k: (info.get(k) or "").strip()
    bb, imei, bbsn, sim = g("BasebandVersion"), g("InternationalMobileEquipmentIdentity"), g("BasebandSerialNumber"), g("SIMStatus")
    bad = lambda v: (not v) or v.lower() in ("n/a", "unknown", "none", "(null)", "0")
    d = {"keys": bb_keys(info), "baseband": bb or "—", "imei": imei or "—", "bb_serial": bbsn or "—", "sim": sim.replace("kCTSIMSupportSIMStatus", "") or "—",
         "bb_status": g("BasebandStatus") or "—"}
    if bad(bb):
        d.update(state="missing", title="لا يوجد فيرموير مودم (Baseband غير موجود)",
                 poss=[],
                 checks=[])
    elif bad(imei):
        d.update(state="partial", title="المودم ظاهر لكن IMEI غير مقروء",
                 poss=[],
                 checks=[])
    else:
        d.update(state="ok", title="المودم ظاهر (Baseband و IMEI مقروءان)", poss=[], checks=[])
    return d

COLOR_NAMES = {("iPhone18,2", "1"): "DeepBlue"}  # من قراءة 3uTools على هاتف المستخدم (iPhone 17 Pro Max)

def _b64txt(v):
    import base64
    try:
        return base64.b64decode(v + "=" * (-len(v) % 4)).decode("utf-8", "ignore").strip()
    except Exception:
        return ""

def _findmy_state(info):
    """Find My / قفل التنشيط من متغيّر NVRAM: fm-activation-locked (قيمته base64). YES = مفعّل."""
    raw = (info.get("fm-activation-locked") or "").strip()
    if not raw:
        return "—"
    t = _b64txt(raw).upper()
    if t == "YES":
        return "مفعّل"
    if t == "NO":
        return "غير مفعّل"
    return "—"

def _commercial_gb(nbytes):
    """أقرب سعة تجارية (GB) أكبر أو تساوي السعة الفعلية المقروءة."""
    g = nbytes / 1e9
    for t in (16, 32, 64, 128, 256, 512, 1024, 2048, 4096):
        if g <= t * 1.0:
            return t
    return round(g)

def _cap_text(gb):
    return f"{gb // 1024} TB" if gb >= 1024 and gb % 1024 == 0 else f"{gb} GB"

def _get_device(fast=False):
    if DEMO["on"]:
        return DEMO_DEVICE
    has_id = bool(shutil.which("idevice_id"))
    ids = run(["idevice_id", "-l"]).split() if has_id else []
    if not ids:
        irec = shutil.which("irecovery")
        if irec:
            q = kv(run([irec, "-q"]))
            mode = q.get("MODE")
            if mode:
                return {"connected": True, "mode": mode, "name": q.get("PRODUCT", "Apple device"),
                        "fields": {"Model Identifier": q.get("PRODUCT", "—"), "Product Type": q.get("MODEL", "—"), "iOS Version": "—",
                                   "Serial Number": q.get("SRNM", "—"), "ECID": q.get("ECID", "—"), "UDID": "—", "Chip": q.get("CPID", "—")},
                        "right": {"color": "—", "capacity": "—", "findmy": "—", "activation": "—", "jb": "—", "mode": mode + " Mode"},
                        "battery": None, "storage": None}
        u = usb_apple_mode()
        if u:
            return u
        if not has_id and not irec:
            return {"connected": False, "reason": "أدوات libimobiledevice غير مثبتة — اتصال الجهاز غير متاح (باقي الأدوات تعمل)."}
        return {"connected": False, "reason": "لا يوجد جهاز متصل. إذا كان بوضع Recovery/DFU ولم يظهر، تأكد من تعريف Apple (يأتي مع 3uTools أو Apple Devices)."}
    info = kv(run(["ideviceinfo"]))
    if not info:
        return {"connected": False, "reason": "الجهاز غير موثوق أو مقفل — افتح القفل واضغط Trust."}
    pt = info.get("ProductType", "—")
    known_learn(info, pt)
    ecid = info.get("UniqueChipID", "")
    ecid_hex = format(int(ecid), "016X") if ecid.isdigit() else "—"
    cap = info.get("TotalDiskCapacity", "")
    cap_txt = _cap_text(_commercial_gb(int(cap))) if cap.isdigit() else "—"
    batt = None
    storage = None
    if not fast:
        bi = kv(run(["ideviceinfo", "-q", "com.apple.mobile.battery"]))
        if bi.get("BatteryCurrentCapacity", "").isdigit():
            batt = {"pct": int(bi["BatteryCurrentCapacity"]), "cycles": None, "health": None}
            try:
                pl = plistlib.loads(run(["idevicediagnostics", "ioregentry", "AppleSmartBattery"]).encode())
                d = pl.get("IORegistry", pl) if isinstance(pl, dict) else {}
                batt["cycles"] = d.get("CycleCount")
                mx, ds = d.get("AppleRawMaxCapacity") or d.get("NominalChargeCapacity"), d.get("DesignCapacity")
                if mx and ds:
                    batt["health"] = min(100, round(100 * mx / ds))
            except Exception:
                pass
        du = kv(run(["ideviceinfo", "-q", "com.apple.disk_usage"]))
        try:
            tot = int(du["TotalDataCapacity"]) / 1e9
            free = int(du["AmountDataAvailable"]) / 1e9
            storage = {"used": round(tot - free, 1), "total": round(tot, 1)}
        except Exception:
            pass
        try:
            _cb = int(du.get("TotalDiskCapacity") or du.get("TotalDataCapacity") or 0)
            if _cb > 0:
                cap_txt = _cap_text(_commercial_gb(_cb))
        except Exception:
            pass
    return {
        "connected": True, "mode": "Normal", "name": PRODUCT_NAMES.get(pt, pt),
        "fields": {"Model Identifier": pt, "Product Type": info.get("HardwareModel", "—"),
                   "iOS Version": f'{info.get("ProductVersion", "—")} ({info.get("BuildVersion", "—")})',
                   "Serial Number": info.get("SerialNumber", "—"), "ECID": ecid_hex,
                   "UDID": info.get("UniqueDeviceID", "—"), "Chip": info.get("CPUArchitecture", "—"),
                   "IMEI": info.get("InternationalMobileEquipmentIdentity", "—"),
                   "IMEI 2": info.get("InternationalMobileEquipmentIdentity2", "—"),
                   "Model Number": (info.get("ModelNumber", "") + info.get("RegionInfo", "")) or "—"},
        "right": {"color": COLOR_NAMES.get((pt, info.get("DeviceColor", "")), info.get("DeviceColor", "—")), "capacity": cap_txt,
                  "findmy": _findmy_state(info), "activation": info.get("ActivationState", "—"), "jb": "—", "mode": "Normal Mode"},
        "battery": batt, "storage": storage, "modem": modem_check(info, pt),
    }

_DEV = {"lock": threading.Lock(), "t": 0.0, "v": None}

def get_device(fast=False):
    """قراءة الجهاز مرة واحدة في كل وقت: الطلبات المتزامنة تنتظر نتيجة القراءة الجارية بدل تشغيل أوامر موازية."""
    if DEMO["on"]:
        return DEMO_DEVICE
    if not fast and _DEV["v"] is not None and time.time() - _DEV["t"] < 3:
        return _DEV["v"]
    if not _DEV["lock"].acquire(blocking=False):
        with _DEV["lock"]:
            pass
        if _DEV["v"] is not None and time.time() - _DEV["t"] < 30:
            return _DEV["v"]
        _DEV["lock"].acquire()
    try:
        v = _get_device(fast)
        if not fast:
            _DEV["t"], _DEV["v"] = time.time(), v
        return v
    finally:
        _DEV["lock"].release()


# ---------------------------------------------------------------- اكتشاف مكان السجل
DISC = {"before": None, "results": []}

def open_path(p):
    if os.name == "nt":
        os.startfile(p)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])

SCHEM_EXT = (".pdf", ".zxw", ".jcid", ".bvr", ".brd", ".pcb", ".cad", ".png", ".jpg", ".jpeg", ".fz", ".tvw", ".asc", ".bdv")

# ---------------------------------------------------------------- فحص المودم من سجل الجهاز
MODEM_KW = re.compile(r"baseband|commcenter|modem|\bARI\b|CTServer|telephony|\bIMEI\b|rffe|coredump|\bBBU\b|CoreTelephony|AppleBaseband", re.I)
MODEM_STRONG = re.compile(r"(?:baseband|modem|commcenter|\bBBU\b|\bARI\b|CTServer)[^\n]{0,80}\b(?:fail(?:ed|ure|s)?|not respond\w*|time[d ]?-?out|crash\w*|panic\w*|unresponsive|no response)\b|no baseband|baseband (?:is )?(?:not|unavailable|missing)|coredump", re.I)
MODEM_BENIGN = re.compile(r"invalidated|entitlement|<Debug>|required for notification|Failed to get name|kDormant|Dormancy", re.I)
MODEM_TAG = re.compile(r"<(?:Error|Fault)>")
MODEM_BAD = re.compile(r"fail|error|timeout|timed out|not respond|unable|crash|reset|invalid|no response|dead|stuck|unavailable|not ready|lost", re.I)

def capture_syslog(sec=15, tick=None):
    exe = shutil.which("idevicesyslog")
    if not exe:
        return None, "idevicesyslog غير موجود ضمن أدوات libimobiledevice."
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    try:
        p = subprocess.Popen([exe], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
    except Exception as e:
        return None, f"تعذر تشغيل idevicesyslog: {e}"
    lines = []
    def rd():
        for raw in iter(p.stdout.readline, b""):
            if len(lines) < 60000:
                lines.append(raw.decode("utf-8", errors="ignore").rstrip())
    t = threading.Thread(target=rd, daemon=True)
    t.start()
    for i in range(sec):
        time.sleep(1)
        if tick:
            tick(i + 1)
    try:
        p.kill()
    except Exception:
        pass
    t.join(2)
    return lines, ""

class SyslogKeeper:
    """يلتقط سجل الهاتف بشكل متواصل: يبدأ قبل أمر إعادة التشغيل، وعند انقطاع USB يعيد التشغيل
    فور ظهور الجهاز (فحص كل 0.3 ثانية) بدون أي انتظار، ليشمل السجل أبكر لحظة ممكنة بعد العودة."""
    def __init__(self):
        self.lines = []
        self.stop = False
        self.t0 = time.time()
        self.first_after_drop = None   # ثواني من البداية حتى أول سطر بعد انقطاع الاتصال
        self.dropped = False
        self.reconnects = 0
        self.proc = None
        self.th = threading.Thread(target=self._run, daemon=True)
    def start(self):
        self.th.start()
        return self
    def _present(self):
        try:
            return bool((run(["idevice_id", "-l"], timeout=5) or "").split())
        except Exception:
            return False
    def _run(self):
        exe = shutil.which("idevicesyslog")
        if not exe:
            return
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        while not self.stop:
            if not self._present():
                if self.lines:
                    self.dropped = True
                time.sleep(0.3)
                continue
            try:
                p = subprocess.Popen([exe], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
            except Exception:
                time.sleep(1)
                continue
            self.proc = p
            if self.dropped:
                self.reconnects += 1
            last = [time.time()]
            def rd():
                for raw in iter(p.stdout.readline, b""):
                    last[0] = time.time()
                    if len(self.lines) < 60000:
                        if self.dropped and self.first_after_drop is None:
                            self.first_after_drop = round(time.time() - self.t0, 1)
                        self.lines.append(raw.decode("utf-8", errors="ignore").rstrip())
            rt = threading.Thread(target=rd, daemon=True)
            rt.start()
            while not self.stop and p.poll() is None:
                time.sleep(0.3)
                if not self._present():          # انقطع الاتصال
                    self.dropped = True
                    break
                if time.time() - last[0] > 12 and time.time() - self.t0 > 5:   # صمت طويل
                    break
            try:
                p.kill()
            except Exception:
                pass
    def finish(self):
        self.stop = True
        try:
            if self.proc:
                self.proc.kill()
        except Exception:
            pass
        self.th.join(3)
        return self.lines

def read_text_any(f, limit=3_000_000):
    """يقرأ ملف تقرير بأي ترميز شائع (UTF-8 أو UTF-16). يرجع (نص، حجم_بايت)."""
    size = os.path.getsize(f)
    with open(f, "rb") as fh:
        raw = fh.read(limit)
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or (b"\x00" in raw[:200] and len(raw) > 4):
        try:
            return raw.decode("utf-16", errors="ignore"), size
        except Exception:
            pass
    return raw.decode("utf-8", errors="ignore"), size

def crash_dest(dev=None):
    """مجلد سحب التقارير خاص بكل جهاز (UDID) حتى لا تختلط ملفات هاتف بآخر."""
    udid = ((dev or {}).get("fields") or {}).get("UDID", "") if dev else ""
    if not udid or udid == "—":
        try:
            udid = ((get_device().get("fields") or {}).get("UDID", "")) or "unknown"
        except Exception:
            udid = "unknown"
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", udid)[:60] or "unknown"
    return os.path.join(os.path.expanduser("~"), "iphone_crash_reports", safe)

def realtime_log_dirs():
    out = []
    for d in "CDEFG":
        for rel in ("3uTools9", "3uTools", os.path.join("Program Files", "3uTools9"), os.path.join("Program Files (x86)", "3uTools9")):
            out.append(f"{d}:\\{rel}\\RealtimeLog")
    c = cfg_load().get("rt_dir")
    return ([c] if c else []) + [d for d in out if os.path.isdir(d)]

def read_3u_realtime(tail_mb=6):
    """يقرأ آخر ملف سجل حي كتبه 3uTools (نافذة Real-time Logs). يرجع (أسطر، مسار، عمر_بالثواني) أو (None، رسالة، 0)."""
    best = None
    for d in realtime_log_dirs():
        for dp, dn, fn in os.walk(d):
            for f in fn:
                fp = os.path.join(dp, f)
                try:
                    mt = os.path.getmtime(fp)
                except OSError:
                    continue
                if best is None or mt > best[0]:
                    best = (mt, fp)
    if not best:
        return None, "لم أجد مجلد RealtimeLog. افتح Real-time Logs في 3uTools.", 0
    mt, fp = best
    try:
        with open(fp, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - tail_mb * 1024 * 1024))
            data = fh.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return None, f"تعذر قراءة {fp}: {e}", 0
    return data.splitlines(), fp, int(time.time() - mt)

def summarize_syslog(lines, src):
    kwl = [l for l in lines if MODEM_KW.search(l)]
    bad = [l for l in kwl if MODEM_STRONG.search(l) and not MODEM_BENIGN.search(l)]
    badset = set(bad)
    tagged = [l for l in kwl if MODEM_TAG.search(l) and l not in badset]
    return {"available": True, "source": src, "total": len(lines), "kw": len(kwl), "bad": len(bad),
            "tagged": len(tagged), "tagged_lines": [l[:260] for l in tagged[-15:]],
            "bad_lines": [l[:260] for l in bad[-40:]], "sample": [l[:260] for l in kwl[-25:]]}

def syslog_findings(sl, secs):
    f = []
    if sl["kw"] == 0:
        f.append(f"لم يظهر أي نشاط للمودم (CommCenter / Baseband) في سجل الجهاز ({secs}). هذا يرجّح أن النظام لا يرى المودم أصلاً (لا يبدأ). تقدير فقط.")
    elif sl["bad"]:
        f.append(f"ظهرت {sl['bad']} رسالة أخطاء مرتبطة بالمودم في سجل الجهاز (انظر الأسطر). هذا يدعم وجود عطل بالمودم أو باتصاله بالنظام.")
        bl = sl.get("bad_lines") or []
        if any(re.search(r"Client disconnect from modem|QMux|coredump\.drv", l, re.I) for l in bl):
            f.append("أهم الأسطر: CommCenter يحاول فتح قناة الاتصال مع المودم (coredump / QMux) ويرى الحالة Stopped مع «Client disconnect from modem». أي أن قناة الاتصال بالمودم لا تقوم. هذا يخص الأجهزة بمودم Qualcomm، وهو أقوى دليل في السجل (تقدير).")
        if any("NEPathEvent Cellular Failed" in l for l in bl):
            f.append("أسطر «NEPathEvent Cellular Failed» نتيجة وليست سبباً: تطبيقات النظام حاولت استعمال الشبكة الخلوية وفشلت لأن المودم غير جاهز.")
    else:
        f.append("يوجد نشاط للمودم في السجل بدون رسائل أخطاء واضحة.")
    return f

def save_scan_log(lines, tag):
    """يحفظ سجل الفحص كاملاً بملف نصي (آخر 30 ملفاً فقط). يرجع المسار أو ''."""
    try:
        d = os.path.join(os.path.expanduser("~"), "NawafTeam_logs")
        os.makedirs(d, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9_-]", "_", tag)[:40] or "scan"
        fp = os.path.join(d, f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        with open(fp, "w", encoding="utf-8", errors="ignore") as f:
            f.write("\n".join(lines))
        old = sorted(glob.glob(os.path.join(d, "*.txt")), key=os.path.getmtime)
        for o in old[:-30]:
            try:
                os.remove(o)
            except Exception:
                pass
        return fp
    except Exception:
        return ""

def analyze_log_text(text, name="ملف"):
    lines = [l.rstrip() for l in text.splitlines() if l.strip()][:200000]
    if len(lines) < 50:
        return {"ok": False, "error": f"الملف قصير جداً ({len(lines)} سطر) ولا يبدو سجل جهاز."}
    sl = summarize_syslog(lines, "ملف: " + name)
    n_on = (1 if sl["bad"] else 0) + (1 if sl["kw"] == 0 else 0)
    if sl["bad"] and any(re.search(r"Client disconnect from modem|QMux|coredump\.drv", l, re.I) for l in sl["bad_lines"]):
        lvl, lab = "suspect", "أسطر في السجل تدل على أن قناة المودم لا تقوم (غير حاسم من السجل وحده)"
    elif sl["bad"]:
        lvl, lab = "suspect", "رسائل أخطاء مرتبطة بالمودم في السجل (غير حاسم من السجل وحده)"
    elif sl["kw"] == 0:
        lvl, lab = "suspect", "لا نشاط للمودم في السجل (قد يعني أنه لا يبدأ، أو أن السجل لا يغطي الإقلاع)"
    else:
        lvl, lab = "ok", "لا رسائل أخطاء مودم واضحة في هذا السجل"
    return {"ok": True, "syslog": sl, "findings": syslog_findings(sl, "الملف"),
            "verdict": {"level": lvl, "label": lab}, "name": name,
            "note": "التحليل من السجل فقط؛ السجل لا يغني عن المقارنة مع جهاز سليم أو عن القياس."}

def restart_device(P):
    """يعيد تشغيل الهاتف من الكمبيوتر وينتظر عودته. يرجع (نجح، رسالة)."""
    if DEMO["on"]:
        for i in range(3):
            P(3 + i, "محاكاة إعادة التشغيل")
            time.sleep(1)
        return True, ""
    P(3, "إرسال أمر إعادة التشغيل")
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    def _ok(cmd, t):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=t, **kw).returncode == 0
        except Exception:
            return False
    done = False
    exe = find_tool("idevicediagnostics")
    if exe:
        done = _ok([exe, "restart"], 20)
    if not done and _pmd3_ok():
        done = _ok([sys.executable, "-m", "pymobiledevice3", "diagnostics", "restart"], 40)
    if not done:
        return False, "تعذر إعادة التشغيل من الكمبيوتر (idevicediagnostics غير متوفر). أعد تشغيل الهاتف يدوياً ثم اضغط «ابدأ التحليل» مباشرة بعد ظهور الجهاز."
    def ids():
        return (run(["idevice_id", "-l"]) or "").split()
    t0 = time.time()
    while time.time() - t0 < 25 and ids():   # ينتظر انقطاع الاتصال
        time.sleep(1)
    P(5, "الهاتف يُعيد التشغيل… انتظر عودته")
    t0 = time.time()
    while time.time() - t0 < 150:
        if ids():
            time.sleep(3)
            return True, ""
        time.sleep(2)
        P(6, f"بانتظار عودة الهاتف ({int(time.time() - t0)}ث)")
    return False, "لم يعد الهاتف خلال 150 ثانية. تأكد أنه أقلع وفُتح قفله ثم أعد المحاولة."

def modem_scan(progress=None, restart=False, secs=30):
    P = progress or (lambda pct, st: None)
    keeper = None
    if restart:
        if not DEMO["on"]:
            keeper = SyslogKeeper().start()   # يبدأ الالتقاط قبل إعادة التشغيل ويتابع عبر الانقطاع
        ok_r, msg_r = restart_device(P)
        if not ok_r:
            if keeper:
                keeper.finish()
            return {"ok": False, "error": msg_r}
    P(3, "قراءة معلومات الجهاز")
    dev = get_device(fast=True)
    if not dev.get("connected") or dev.get("mode") != "Normal":
        return {"ok": False, "error": "الفحص يحتاج الجهاز موصولاً وشغّالاً (Normal) وموثوقاً."}
    out = {"ok": True, "modem": dev.get("modem"), "steps": [], "findings": [],
           "device_pt": (dev.get("fields") or {}).get("Model Identifier", "")}
    SEC = max(10, min(120, int(secs or 30)))
    if restart:
        SEC = max(SEC, 40)
    P(8, f"قراءة سجل الجهاز ({SEC} ثانية)")
    if keeper:
        t_wait = time.time()
        while time.time() - t_wait < SEC:
            time.sleep(1)
            i = int(time.time() - t_wait)
            P(8 + int(min(i, SEC) * 52 / SEC), f"قراءة سجل الجهاز ({SEC} ثانية)")
        lines, err = keeper.finish(), ""
        out["capture"] = {"mode": "continuous", "reconnects": keeper.reconnects, "first_line_after_drop_s": keeper.first_after_drop}
        if not lines:
            lines = None
            err = "لم يُلتقط أي سطر من السجل."
    else:
        lines, err = capture_syslog(SEC, lambda i: P(8 + int(i * 52 / SEC), f"قراءة سجل الجهاز ({SEC} ثانية)"))
    src = "idevicesyslog"
    MIN_LINES = 200  # أقل من هذا = السجل لم يُلتقط فعلياً (جهاز سليم يعطي آلاف الأسطر في 15 ثانية)
    if lines is None or len(lines) < MIN_LINES:
        rl, rp, age = read_3u_realtime()
        if rl is not None and len(rl) >= MIN_LINES and age <= 90:
            lines, src = rl, "3uTools RealtimeLog (" + os.path.basename(rp) + ")"
        elif lines is None:
            err = err + " | " + rp
        elif len(lines) < MIN_LINES:
            err = f"السجل رجع {len(lines)} سطر فقط خلال {SEC} ثانية، يعني لم يُلتقط فعلياً (جهاز مقفل، غير موثوق، أو لا يرسل سجلاً). لذلك لا نحكم من السجل. افتح قفل الهاتف، اضغط Trust، وافتح Real-time Logs في 3uTools ثم أعد الفحص."
            lines = None
    if lines is None:
        out["syslog"] = {"available": False, "error": err}
    else:
        out["syslog"] = summarize_syslog(lines, src)
        out["log_path"] = save_scan_log(lines, out.get("device_pt") or "scan")
    # سجل عتاد النظام (IORegistry): هل يظهر أي عنصر مرتبط بالمودم؟
    ioreg = {"available": False}
    P(62, "فحص سجل عتاد النظام (IORegistry)")
    _dx = find_tool("idevicediagnostics")
    if _dx:
        raw = run([_dx, "ioreg", "IOService"], timeout=120)
        _t = diag_tree_matches(raw, re.compile(r"a^")) if _is_plist_out(raw) else None
        if _t and _t["nodes"] > 1:
            toks = sorted(set(re.findall(r"[A-Za-z0-9_]*(?:Baseband|Modem|BBPMU|BasebandPCIe|CommCenter)[A-Za-z0-9_]*", raw, re.I)))
            ioreg = {"available": True, "size": len(raw), "tokens": toks[:40], "nodes": _t["nodes"]}
        else:
            ioreg = {"available": False, "reason": "الأداة لم ترجع شجرة IORegistry كافية (عنصر الجذر فقط أو ناتج غير مفهوم)"}
    out["ioreg"] = ioreg
    # تقارير الـ panic
    P(78, "سحب وتحليل تقارير الأعطال")
    dest = crash_dest(dev)
    try:
        ok, msg, _miss = pull_crash_reports(dest)
    except Exception as e:
        ok, msg = False, str(e)
    panics = []
    if os.path.isdir(dest):
        for f in find_panics(dest)[:3]:
            try:
                txt, _sz = read_text_any(f)
                r = analyze_panic(txt)
                bb = [m for c, m in r["notes"] if c == "BB"]
                panics.append({"file": os.path.basename(f), "summary": r["summary"][:200], "size": os.path.getsize(f),
                               "top": [{"cat": CAT_AR[c], "pct": p} for c, v, p in r["scores"][:3]], "bb": bb[:3]})
            except Exception:
                pass
    out["panics"] = panics
    out["pull_ok"] = ok
    # الخلاصة (تقديرية)
    P(94, "تجميع الخلاصة")
    sl, m = out["syslog"], out["modem"] or {}
    if sl.get("available"):
        out["findings"] += syslog_findings(sl, f"{SEC} ثانية")
    else:
        out["findings"].append("سجل الجهاز لم يُلتقط (انظر السبب في التفاصيل)، لذلك لا يُبنى أي استنتاج منه.")
    bbs = (out.get("modem") or {}).get("keys") or {}
    bbs = bbs if bbs else {"BasebandStatus": (out.get("modem") or {}).get("bb_status", "")}
    st = str(bbs.get("BasebandStatus", "")).replace("—", "")
    if st and "available" not in st.lower():
        out["findings"].append(f"الجهاز نفسه يقول BasebandStatus = {st} (على جهاز سليم تكون BBInfoAvailable). أي أن النظام يرى المودم غير جاهز.")
    io = out["ioreg"]
    if io.get("available"):
        if not io["tokens"]:
            out["findings"].append("لا يظهر أي عنصر باسم Baseband/Modem في سجل عتاد النظام (IORegistry). إذا تأكد هذا بعدة فحوصات، فالنظام غالباً لا يرى المودم على الناقل: يرجّح عطل عتاد (تغذية، إشارة، لحام، اتصال بين البوردين). تقدير غير مؤكد.")
        else:
            out["findings"].append("النظام يظهر عناصر مرتبطة بالمودم في IORegistry (انظر القائمة). إذا كان الفيرموير مفقوداً مع هذا، فالاحتمال يميل لمشكلة تحميل الفيرموير أو بيانات المعايرة أكثر من انقطاع كامل. تقدير غير مؤكد.")
    if any(p["bb"] for p in panics):
        out["findings"].append("تقارير panic على الجهاز تذكر المودم/الباسباند (انظر التفاصيل).")
    # مؤشرات الأدلة (شفافة، بدون نسبة ثقة مفتعلة)
    io = out["ioreg"]
    sig = [
        {"t": "فيرموير المودم / Baseband غير موجود على الجهاز", "on": m.get("state") == "missing"},
        {"t": "IMEI غير مقروء", "on": m.get("state") in ("missing", "partial")},
        {"t": "أخطاء مودم في سجل الجهاز", "on": bool(sl.get("available") and sl.get("bad"))},
        {"t": "النظام لا يرى أي عنصر مودم (IORegistry)", "on": bool(io.get("available") and not io.get("tokens"))},
        {"t": "تقارير panic تذكر المودم", "on": any(p["bb"] for p in panics)},
    ]
    keys = m.get("keys") or {}
    out["bb_keys"] = keys
    cmp_ = bb_compare(out.get("device_pt", ""), keys)
    out["baseline"] = cmp_
    if cmp_:
        sig.append({"t": "اختلاف عن جهاز سليم من نفس الموديل (مفاتيح مفقودة)", "on": bool(cmp_["missing"])})
        if cmp_["missing"]:
            out["findings"].insert(0, f"بالمقارنة مع جهاز سليم محفوظ لنفس الموديل: {len(cmp_['missing'])} مفتاح مودم مفقود على هذا الجهاز (انظر المقارنة).")
    out["signals"] = sig
    n_on = sum(1 for x in sig if x["on"])
    if m.get("state") == "missing" or n_on >= 3:
        lvl, lab = "fault", "المودم لا يعمل: غير جاهز أو لا يتواصل مع النظام"
    elif n_on >= 1:
        lvl, lab = "suspect", "مؤشرات على مشكلة بالمودم لكنها غير حاسمة"
    elif m.get("state") == "ok":
        lvl, lab = "ok", "لا توجد مشكلة مودم ظاهرة بهذا الفحص"
    else:
        lvl, lab = "unknown", "النتيجة غير كافية للحكم"
    out["verdict"] = {"level": lvl, "label": lab, "on": n_on, "total": len(sig)}
    out["flow_pt"] = out.get("device_pt") if (lvl in ("fault", "suspect") and out.get("device_pt") in FLOWS) else ""
    out["steps"] = (m.get("checks") or [])
    out["poss"] = (m.get("poss") or [])
    return out

# ---------------------------------------------------------------- تحليل الباسباند (مهمة خلفية)
BB = {"state": "idle", "pct": 0, "stage": "", "result": None}

def bb_job(model, restart=False, secs=30):
    BB.update(state="running", pct=0, stage="بدء التحليل", result=None)
    try:
        r = modem_scan(lambda pct, st: BB.update(pct=pct, stage=st), restart, secs)
        if r.get("ok") and restart:
            r["restarted"] = True
            r["findings"].insert(0, "الفحص تم بعد إعادة تشغيل الهاتف، فالسجل يشمل مرحلة الإقلاع (عندما تتم تهيئة المودم).")
        if r.get("ok"):
            r["selected"] = model
            r["selected_name"] = PRODUCT_NAMES.get(model, model)
            dpt = r.get("device_pt") or ""
            if model and dpt and model != dpt:
                r["findings"].insert(0, f"تنبيه: الموديل المختار ({PRODUCT_NAMES.get(model, model)}) يختلف عن الجهاز الموصول ({PRODUCT_NAMES.get(dpt, dpt)}).")
            r["notes"] = (cfg_load().get("model_notes") or {}).get(model, "")
        BB.update(state="done" if r.get("ok") else "error", pct=100, stage="اكتمل" if r.get("ok") else r.get("error", ""), result=r)
    except Exception as e:
        BB.update(state="error", stage=f"خطأ: {e}", result=None)

def panic_json(text):
    r = analyze_panic(text)
    dev = ""
    if r.get("product"):
        dev = PRODUCT_NAMES.get(r["product"], r["product"]) + " (" + r["product"] + (" / " + r["target"] if r.get("target") else "") + ")"
    return {"summary": r["summary"][:600], "device": dev,
            "scores": [{"cat": CAT_AR[c], "pct": p} for c, v, p in r["scores"]],
            "notes": [{"cat": CAT_AR[c], "msg": m} for c, m in r["notes"]],
            "guide": [{k: x for k, x in g.items() if k != "_n"} for g in r.get("guide", [])]}


# ---------------------------------------------------------------- مخطط القياس الموجّه
# مصدر البيانات: لقطات شاشة قدّمها المستخدم لأداة قياس خارجية على iPhone 13 Pro Max.
# أسماء الخطوط والقيم كما ظهرت فيها حرفياً؛ لم تُتحقق من مخطط الجهاز. kind="info" = ملاحظة عامة من البرنامج.
FLOWS = {}
FLOW_IMG_DIR = os.path.join(os.path.expanduser("~"), ".nawaf_team_flow_img")

def _flow_key(pt, node):
    return re.sub(r"[^A-Za-z0-9_,.-]", "_", f"{pt}__{node}")

def flow_get(pt):
    fl = FLOWS.get(pt)
    if not fl:
        return {"ok": False, "error": "لا يوجد مخطط قياس جاهز لهذا الموديل بعد."}
    out = json.loads(json.dumps(fl))
    imgs = {}
    for nid in out["nodes"]:
        fp = os.path.join(FLOW_IMG_DIR, _flow_key(pt, nid) + ".txt")
        if os.path.isfile(fp):
            try:
                imgs[nid] = open(fp, encoding="utf-8").read()
            except Exception:
                pass
    out["images"] = imgs
    out["ok"] = True
    return out

def flow_img_set(pt, node, data):
    fl = FLOWS.get(pt)
    if not fl or node not in fl["nodes"]:
        return {"ok": False, "error": "خطوة غير معروفة."}
    if data and (not data.startswith("data:image/") or len(data) > 3_000_000):
        return {"ok": False, "error": "الصورة غير صالحة أو أكبر من 2 ميغابايت."}
    os.makedirs(FLOW_IMG_DIR, exist_ok=True)
    fp = os.path.join(FLOW_IMG_DIR, _flow_key(pt, node) + ".txt")
    if not data:
        if os.path.isfile(fp):
            os.remove(fp)
        return {"ok": True}
    with open(fp, "w", encoding="utf-8") as f:
        f.write(data)
    return {"ok": True}

def flow_save(pt, body, cfg):
    if pt not in FLOWS:
        return {"ok": False, "error": "موديل غير معروف."}
    if not isinstance(body.get("steps"), list):
        return {"ok": False, "error": "بيانات الجلسة غير صالحة."}
    log = dict(cfg.get("measure_log") or {})
    items = list(log.get(pt) or [])
    items.append({"date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                  "steps": [{"point": str(x.get("point", ""))[:80], "choice": str(x.get("choice", ""))[:80], "value": str(x.get("value", ""))[:40]} for x in body["steps"] if isinstance(x, dict)][:30],
                  "result": str(body.get("result", ""))[:200], "note": str(body.get("note", ""))[:500]})
    log[pt] = items[-20:]
    cfg["measure_log"] = log
    cfg_save(cfg)
    return {"ok": True, "count": len(log[pt])}


HW_CASES = [
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "GPIO_AP_FROM_BB_RESET_DETECT_L",
    "dio13": [["", "0.249", "0.316"]],
    "dio13pm": [["", "0.239", "0.314"]],
    "dio13p": [["", "0.241", "0.319"]],
    "mtype": "Voltage + Impedance + Signal",
    "fix": "الإشارة بين المعالج والبيسباند عبر الطبقتين: افصل البوردين وقيس الإشارة على طبقة المعالج: إذا ما في ممانعة ولا فولت اعمل Reball للمعالج. إذا كانت سليمة، تأكد منها على الطبقة السفلية: إذا ما في ممانعة اعمل Reball للبيسباند.",
    "parts": [],
    "match": [r"TransportError", r"kABPEnterLowPowerFailed"],
    "title": "",
    "symptoms": (""),
    "checks": (""),
    "result": (""),
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "GPIO_SEQ_PMU_TO_BBPMU_RESET_L",
    "dio13": [["", "0.479", "0.747"]],
    "dio13pm": [["", "0.464", "0.747"]],
    "dio13p": [["", "0.465", "0.746"]],
    "mtype": "Voltage + Impedance + Signal",
    "gone": "طبيعي يغيب معها: كل خطوط SMPS والـ LDO، وإشارة الريست بين المعالج والبيسباند.",
    "branchend": "في ممانعة على الخط بس ما في فولط: اعمل Reball للبور الرئيسي.",
    "branch": [["GPIO_SEQ_PMU_TO_BBPMU_RESET_L", "Impedance", "الممانعة مش طبيعية: افحص الخط والقطع يلي عليه."]],
    "flow": "GPIO_SEQ_PMU_TO_BBPMU_RESET_L (الريست بين البور الرئيسي وبور البيسباند)",
    "seq": 1,
    "step": 1,
    "fix": "إذا في ممانعة على الخط بس ما في فولط: اعمل Reball للبور الرئيسي.",
    "meas": None,
    "parts": [],
    "match": [r"failed power on modem"],
    "title": "",
    "symptoms": (""),
    "checks": (""),
    "result": (""),
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "PP_0V75_SMPS7",
    "mtype": "Voltage + Impedance",
    "gone": "طبيعي يغيب معه: SMPS5 وSMPS1_6 وLDO14.",
    "branchend": "الخط والملف سليمين وما في فولت: المشكلة غالباً من أيسي بور البيسباند (U_BBPMU_E).",
    "branch": [["PP_0V75_SMPS7", "Impedance + Coil L307_E", "الاحتمالات: شورت على الخط، أو الملف L307_E مفصول، أو عطل بأيسي بور البيسباند (U_BBPMU_E). لا تحكم على الأيسي قبل ما تتأكد من الخط والملف."]],
    "flow": "PP_0V75_SMPS7",
    "seq": 5,
    "step": 2,
    "ord": 5,
    "when": "SMPS2 وSMPS3 وSMPS4 بس",
    "meas": None,
    "parts": [],
    "match": [r"failed power on modem"],
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "PP_0V75_SMPS5",
    "mtype": "Voltage + Impedance",
    "gone": "طبيعي يغيب معه: SMPS1_6.",
    "branchend": "الخط والملف سليمين وما في فولت: المشكلة غالباً من أيسي بور البيسباند (U_BBPMU_E).",
    "branch": [["PP_0V75_SMPS5", "Impedance + Coil L305_E", "الاحتمالات: شورت على الخط، أو الملف L305_E مفصول، أو عطل بأيسي بور البيسباند (U_BBPMU_E). لا تحكم على الأيسي قبل ما تتأكد من الخط والملف."]],
    "flow": "PP_0V75_SMPS5",
    "seq": 6,
    "step": 2,
    "ord": 6,
    "when": "كل خطوط SMPS ما عدا SMPS1_6 وSMPS5",
    "meas": None,
    "parts": [],
    "match": [r"failed power on modem"],
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},

{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "PP_0V75_SMPS1_6",
    "mtype": "Voltage + Impedance",
    "meas": None,
    "parts": [],
    "match": [r"kABPLinkBWNotification"],
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},

{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "PP_1V22_SMPS2",
    "mtype": "Voltage + Impedance",
    "gone": "طبيعي يغيب معه: SMPS7 وSMPS5 وSMPS1_6، وLDO1 وLDO2 وLDO4 وLDO12 وLDO15 وLDO18.",
    "branchend": "الخط والملف سليمين وما في فولت: المشكلة غالباً من أيسي بور البيسباند (U_BBPMU_E).",
    "branch": [["PP_1V22_SMPS2", "Impedance + Coil L302_E", "الاحتمالات: شورت على الخط، أو الملف L302_E مفصول، أو عطل بأيسي بور البيسباند (U_BBPMU_E). لا تحكم على الأيسي قبل ما تتأكد من الخط والملف."]],
    "flow": "PP_1V22_SMPS2 (قياس قديم)",
    "seq": 4,
    "step": 2,
    "ord": 4,
    "when": "SMPS3 وSMPS4 بس (قياس قديم)",
    "meas": None,
    "parts": [],
    "match": [r"failed power on modem"],
    "title": "",
    "symptoms": "",
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "PP_0V82_SMPS3",
    "mtype": "Voltage + Impedance",
    "gone": "طبيعي يغيب معه: SMPS2 وSMPS7 وSMPS5 وSMPS1_6، وLDO3 وLDO7 وLDO8 وLDO9 وLDO17 وLDO19.",
    "branchend": "الخط والملف سليمين وما في فولت: المشكلة غالباً من أيسي بور البيسباند (U_BBPMU_E).",
    "branch": [["PP_0V82_SMPS3", "Impedance + Coil L303_E", "الاحتمالات: شورت على الخط، أو الملف L303_E مفصول، أو عطل بأيسي بور البيسباند (U_BBPMU_E). لا تحكم على الأيسي قبل ما تتأكد من الخط والملف."]],
    "flow": "PP_0V82_SMPS3",
    "seq": 3,
    "step": 2,
    "ord": 3,
    "when": "SMPS4 بس",
    "meas": None,
    "parts": [],
    "match": [r"failed power on modem"],
    "title": "",
    "symptoms": "",
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "PP_1V9_SMPS4",
    "mtype": "Voltage + Impedance",
    "meas": None,
    "parts": [],
    "match": [],
    "match_idle": True,
    "idle_min": 3,
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "90_PCIE_GP0_AP_TO_BB_REFCLK_P",
    "flow13": "90_PCIE_GP1_AP_TO_BB_REFCLK_P",
    "dio13": [["REFCLK_P", "0.313", "0.298"], ["REFCLK_N", "0.315", "0.299"]],
    "dio13pm": [["REFCLK_P", "0.303", "0.293"], ["REFCLK_N", "0.305", "0.293"]],
    "dio13p": [["REFCLK_P", "0.31", "0.298"], ["REFCLK_N", "0.313", "0.297"]],
    "layer": [["13 Pro · 13 Pro Max", "90_PCIE_GP0_AP_TO_BB_REFCLK_P · 90_PCIE_GP0_AP_TO_BB_REFCLK_N"], ["13", "90_PCIE_GP1_AP_TO_BB_REFCLK_P · 90_PCIE_GP1_AP_TO_BB_REFCLK_N"]],
    "mtype": "Voltage + Impedance + Signal",
    "fix": "الإشارة بين المعالج والبيسباند عبر الطبقتين: افصل البوردين وقيس الإشارة على طبقة المعالج: إذا ما في ممانعة ولا فولت اعمل Reball للمعالج. إذا كانت سليمة، تأكد منها على الطبقة السفلية: إذا ما في ممانعة اعمل Reball للبيسباند.",
    "parts": [],
    "match": [],
    "match_pcie": 5,
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "PCIE_GP0_AP_TO_BB_PERST_L",
    "flow13": "PCIE_GP1_AP_TO_BB_PERST_L",
    "dio13": [["", "0.249", "0.306"]],
    "dio13pm": [["", "0.24", "0.315"]],
    "dio13p": [["", "0.241", "0.319"]],
    "layer": [["13 Pro · 13 Pro Max", "PCIE_GP0_AP_TO_BB_PERST_L"], ["13", "PCIE_GP1_AP_TO_BB_RESET_L"]],
    "mtype": "Voltage + Impedance + Signal",
    "flow": "PCIE_GP0_AP_TO_BB_PERST_L",
    "seq": 11,
    "fix": "الإشارة بين المعالج والبيسباند عبر الطبقتين: افصل البوردين وقيس الإشارة على طبقة المعالج: إذا ما في ممانعة ولا فولت اعمل Reball للمعالج. إذا كانت سليمة، تأكد منها على الطبقة السفلية: إذا ما في ممانعة اعمل Reball للبيسباند.",
    "step": 3,
    "sub": "pcie",
    "ord": 1,
    "parts": [],
    "match": [r"failed power on modem"],
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "90_PCIE_BB_TO_AP_RX_SDX_N",
    "dio13": [["RX_P", "OL", "0.372"], ["RX_N", "OL", "0.372"]],
    "dio13pm": [["RX_P", "OL", "0.37"], ["RX_N", "OL", "0.37"]],
    "dio13p": [["RX_P", "OL", "0.369"], ["RX_N", "OL", "0.369"]],
    "layer": [["13 Pro · 13 Pro Max", "90_PCIE_GP0_AP_FROM_BB_RX_P · 90_PCIE_GP0_AP_FROM_BB_RX_N"], ["13", "90_PCIE_GP1_AP_FROM_BB_RX_P · 90_PCIE_GP1_AP_FROM_BB_RX_N"]],
    "mtype": "Impedance + Signal",
    "flow": "90_PCIE_BB_TO_AP_RX_SDX_N · 90_PCIE_BB_TO_AP_RX_SDX_P (خطوط RX بين البيسباند والمعالج)",
    "seq": 12,
    "step": 3,
    "sub": "pcie",
    "ord": 2,
    "parts": [],
    "match": [r"failed power on modem"],
    "fix": "تأكد من المكثفين C1001_E وC1002_E. بعدها قيس الممانعة على طرف المعالج (الطرف 2: PP1314_E لـC1001_E وPP1315_E لـC1002_E): إذا ما في ممانعة اعمل Reball للمعالج. إذا في ممانعة، قيس على طرف البيسباند (الطرف 1): إذا ما في ممانعة اعمل Reball للبيسباند.",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "90_PCIE_BB_TO_AP_RX_SDX_P",
    "dio13": [["RX_P", "OL", "0.372"], ["RX_N", "OL", "0.372"]],
    "dio13pm": [["RX_P", "OL", "0.37"], ["RX_N", "OL", "0.37"]],
    "dio13p": [["RX_P", "OL", "0.369"], ["RX_N", "OL", "0.369"]],
    "layer": [["13 Pro · 13 Pro Max", "90_PCIE_GP0_AP_FROM_BB_RX_P · 90_PCIE_GP0_AP_FROM_BB_RX_N"], ["13", "90_PCIE_GP1_AP_FROM_BB_RX_P · 90_PCIE_GP1_AP_FROM_BB_RX_N"]],
    "mtype": "Impedance + Signal",
    "flow": "90_PCIE_BB_TO_AP_RX_SDX_N · 90_PCIE_BB_TO_AP_RX_SDX_P (خطوط RX بين البيسباند والمعالج)",
    "seq": 12,
    "step": 3,
    "sub": "pcie",
    "ord": 3,
    "parts": [],
    "match": [r"failed power on modem"],
    "fix": "تأكد من المكثفين C1001_E وC1002_E. بعدها قيس الممانعة على طرف المعالج (الطرف 2: PP1314_E لـC1001_E وPP1315_E لـC1002_E): إذا ما في ممانعة اعمل Reball للمعالج. إذا في ممانعة، قيس على طرف البيسباند (الطرف 1): إذا ما في ممانعة اعمل Reball للبيسباند.",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "SHIELD_SLEEP_CLK_32K",
    "mtype": "Voltage + Impedance",
    "flow": "PP_1V9_SMPS4",
    "seq": 2,
    "fix": "وبعدها قيس الفولط والممانعة على الساعة SHIELD_SLEEP_CLK_32K.",
    "step": 2,
    "ord": 2,
    "when": "ولا خط SMPS (كلها بدون فولت)",
    "meas": None,
    "parts": [],
    "match": [r"failed power on modem"],
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "BB_BBPMU_SPMI_DATA",
    "mtype": "Voltage + Impedance + Signal",
    "flow": "BB_BBPMU_SPMI_DATA (الحكي بين البيسباند والبور)",
    "seq": 10,
    "step": 3,
    "sub": "spmi",
    "ord": 1,
    "parts": [],
    "match": [r"failed power on modem"],
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "SHIELD_SDR_38P4M_CLK",
    "mtype": "Voltage + Impedance + Signal",
    "flowend": "إذا كل الخطوط سليمة ولسا المشكلة موجودة: يجب تبديل الـSDR (U_SDR_E).",
    "fixbad": "",
    "flow": "SHIELD_SDR_38P4M_CLK (ساعة الـ SDR)",
    "seq": 1,
    "parts": [],
    "match": [],
    "match_crash": True,
    "fix": "إذا كانت الخطوط سليمة ولم تُحل المشكلة، يجب تبديل الـSDR (U_SDR_E).",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "QLINK_EN_BB_TO_SDR",
    "mtype": "Voltage + Impedance + Signal",
    "flowend": "إذا كل الخطوط سليمة ولسا المشكلة موجودة: يجب تبديل الـSDR (U_SDR_E).",
    "fixbad": "",
    "flow": "QLINK_EN_BB_TO_SDR (البيسباند بيشغّل الـ SDR)",
    "seq": 2,
    "parts": [],
    "match": [],
    "match_crash": True,
    "fix": "إذا كانت الخطوط سليمة ولم تُحل المشكلة، يجب تبديل الـSDR (U_SDR_E).",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "BB_TO_SDR_WMSS_RESET_L",
    "mtype": "Voltage + Impedance + Signal",
    "flowend": "إذا كل الخطوط سليمة ولسا المشكلة موجودة: يجب تبديل الـSDR (U_SDR_E).",
    "fixbad": "",
    "flow": "BB_TO_SDR_WMSS_RESET_L (ريست الـ SDR)",
    "seq": 3,
    "parts": [],
    "match": [],
    "match_crash": True,
    "fix": "إذا كانت الخطوط سليمة ولم تُحل المشكلة، يجب تبديل الـSDR (U_SDR_E).",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "QLINK_REQ_SDR_TO_BB_R",
    "mtype": "Voltage + Impedance + Signal",
    "flowend": "إذا كل الخطوط سليمة ولسا المشكلة موجودة: يجب تبديل الـSDR (U_SDR_E).",
    "fixbad": "تأكد من المقاومة R1601_E.",
    "flow": "QLINK_REQ_SDR_TO_BB_R (رد الـ SDR على البيسباند)",
    "seq": 4,
    "parts": [],
    "match": [],
    "match_crash": True,
    "fix": "تأكد من المقاومة R1601_E. إذا كانت الخطوط سليمة ولم تُحل المشكلة، يجب تبديل الـSDR (U_SDR_E).",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "BB_EEPROM_I2C_SCL",
    "mtype": "Voltage + Impedance + Signal",
    "parts": [],
    "match": [r"BBUpdater error 51", r"kBBUReturnFailCreateTransportSecond"],
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "BB_EEPROM_I2C_SDA",
    "mtype": "Voltage + Impedance + Signal",
    "parts": [],
    "match": [r"BBUpdater error 51", r"kBBUReturnFailCreateTransportSecond"],
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "PP0V83_NAND_PLL",
    "parts": [],
    "match": [],
    "match_flash_open": "update_NAND_firmware",
    "fix": "افحص فولطات تغذية الـNAND، وتأكد من سلامة FL3023.",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "I2C0_S_SDA",
    "parts": [],
    "match": [],
    "match_flash_open": "load_sep_os",
    "fix": "الخطأ من إيبروم المعالج: افحص خطي الداتا (I2C0_S_SDA) والكلوك بين المعالج والإيبروم، وتأكد من المقاومات الموجودة على الخطين، وتأكد من سلامة الإيبروم.",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "PS_HOLD_R",
    "flow13": "PS_HOLD",
    "fix13": "على iPhone 13 الخط اسمه PS_HOLD وما عليه مقاومة: رايح مباشرة من بور البيسباند (U_BBPMU_E) للبيسباند (U_BB_E). قيس الخط، وإذا سليم تأكد من سلامة بور البيسباند (U_BBPMU_E). إذا كانا سليمين اعمل Reball للبيسباند.",
    "mtype": "Voltage + Impedance + Signal",
    "parts": [],
    "match": [],
    "match_odr": "link",
    "fix": "تأكد من المقاومة R1015_E، وإذا كانت سليمة تأكد من سلامة بور البيسباند (U_BBPMU_E). إذا كانا سليمين اعمل Reball للبيسباند.",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "U_BB_E + U_EEPROM_E",
    "parts": [],
    "match": [],
    "match_odr": "nolink",
    "fix": "عدم توافق الإيبروم والبيسباند مع المعالج: تأكد أن البيسباند والإيبروم أصليان لهذا البورد.",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "SKU_ID",
    "mtype": "Resistance",
    "flow": "SKU_ID · PMX_OPTION_DECODE · PLATFORM_ID (مقاومات الـ ID)",
    "seq": 9,
    "step": 3,
    "sub": "spmi",
    "ord": 2,
    "parts": [],
    "match": [r"failed power on modem"],
    "fix": "قيس قيمة المقاومات R501_E وR506_E وR503_E، لازم تكون مطابقة لبورد هالموديل (خاصة بعد سواب طبقة البيسباند).",
    "fix13": "قيس قيمة المقاومات، لازم تكون مطابقة لبورد هالموديل (خاصة بعد سواب طبقة البيسباند): R501_E (5.6K) على SKU_ID، وR503_K (60.4K) على PLATFORM_ID، وR506_E (178K) على PMX_OPTION_DECODE.",
    "fix13p": "قيس قيمة المقاومات، لازم تكون مطابقة لبورد هالموديل (خاصة بعد سواب طبقة البيسباند): R501_E (5.6K) على SKU_ID، وR503_E (27K) على PLATFORM_ID، وR506_E (178K) على PMX_OPTION_DECODE.",
    "fix13pm": "قيس قيمة المقاومات، لازم تكون مطابقة لبورد هالموديل (خاصة بعد سواب طبقة البيسباند): R501_E (5.6K) على SKU_ID، وR503_E (36.5K) على PLATFORM_ID، وR506_E (178K) على PMX_OPTION_DECODE.",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "PMX_OPTION_DECODE",
    "mtype": "Resistance",
    "flow": "SKU_ID · PMX_OPTION_DECODE · PLATFORM_ID (مقاومات الـ ID)",
    "seq": 9,
    "step": 3,
    "sub": "spmi",
    "ord": 3,
    "parts": [],
    "match": [r"failed power on modem"],
    "fix": "قيس قيمة المقاومات R501_E وR506_E وR503_E، لازم تكون مطابقة لبورد هالموديل (خاصة بعد سواب طبقة البيسباند).",
    "fix13": "قيس قيمة المقاومات، لازم تكون مطابقة لبورد هالموديل (خاصة بعد سواب طبقة البيسباند): R501_E (5.6K) على SKU_ID، وR503_K (60.4K) على PLATFORM_ID، وR506_E (178K) على PMX_OPTION_DECODE.",
    "fix13p": "قيس قيمة المقاومات، لازم تكون مطابقة لبورد هالموديل (خاصة بعد سواب طبقة البيسباند): R501_E (5.6K) على SKU_ID، وR503_E (27K) على PLATFORM_ID، وR506_E (178K) على PMX_OPTION_DECODE.",
    "fix13pm": "قيس قيمة المقاومات، لازم تكون مطابقة لبورد هالموديل (خاصة بعد سواب طبقة البيسباند): R501_E (5.6K) على SKU_ID، وR503_E (36.5K) على PLATFORM_ID، وR506_E (178K) على PMX_OPTION_DECODE.",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "PLATFORM_ID",
    "mtype": "Resistance",
    "flow": "SKU_ID · PMX_OPTION_DECODE · PLATFORM_ID (مقاومات الـ ID)",
    "seq": 9,
    "step": 3,
    "sub": "spmi",
    "ord": 4,
    "parts": [],
    "match": [r"failed power on modem"],
    "fix": "قيس قيمة المقاومات R501_E وR506_E وR503_E، لازم تكون مطابقة لبورد هالموديل (خاصة بعد سواب طبقة البيسباند).",
    "fix13": "قيس قيمة المقاومات، لازم تكون مطابقة لبورد هالموديل (خاصة بعد سواب طبقة البيسباند): R501_E (5.6K) على SKU_ID، وR503_K (60.4K) على PLATFORM_ID، وR506_E (178K) على PMX_OPTION_DECODE.",
    "fix13p": "قيس قيمة المقاومات، لازم تكون مطابقة لبورد هالموديل (خاصة بعد سواب طبقة البيسباند): R501_E (5.6K) على SKU_ID، وR503_E (27K) على PLATFORM_ID، وR506_E (178K) على PMX_OPTION_DECODE.",
    "fix13pm": "قيس قيمة المقاومات، لازم تكون مطابقة لبورد هالموديل (خاصة بعد سواب طبقة البيسباند): R501_E (5.6K) على SKU_ID، وR503_E (36.5K) على PLATFORM_ID، وR506_E (178K) على PMX_OPTION_DECODE.",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "DVDD_BYP_PMIC",
    "mtype": "Voltage + Impedance",
    "gone": "طبيعي يغيب معه: باقي خطوط SMPS، وLDO5 وLDO6 وLDO16.",
    "branchend": "كلهم سليمين وما في فولت على SMPS4: المشكلة غالباً من أيسي بور البيسباند (U_BBPMU_E).",
    "branch": [["PP_1V9_SMPS4", "Impedance + Coil L304_E", "الاحتمالات: شورت على الخط، أو الملف L304_E مفصول، أو عطل بأيسي بور البيسباند (U_BBPMU_E). لا تحكم على الأيسي قبل ما تتأكد من الخط والملف."], ["SHIELD_SLEEP_CLK_32K", "Voltage + Impedance + Signal", "المشكلة على خط الساعة: افحصه والقطع يلي عليه."], ["DVDD_BYP_PMIC", "Voltage + Impedance", "المشكلة على DVDD_BYP_PMIC: افحصه والمكثف C502_E."]],
    "flow": "PP_1V9_SMPS4",
    "seq": 2,
    "fix": "قيس الفولط والممانعة على DVDD_BYP_PMIC.",
    "when": "ولا خط SMPS (كلها بدون فولت)",
    "step": 2,
    "sub": "",
    "ord": 1,
    "parts": [],
    "match": [r"failed power on modem"],
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "SHIELD_MDM_19P2M_CLK",
    "mtype": "Voltage + Impedance + Signal",
    "branchend": "كلهم سليمين وما في ساعة: المشكلة غالباً من شريحة الساعات (U_BBPMK_E).",
    "branch": [["SHIELD_MDM_19P2M_CLK", "Impedance", "المشكلة على خط الساعة: افحصه والقطع يلي عليه."], ["PP_PMX_VIO_OUT", "Voltage + Impedance", "المشكلة على تغذية شريحة الساعات."], ["Y601_E", "Signal", "المشكلة بالكريستال Y601_E."]],
    "flow": "SHIELD_MDM_19P2M_CLK (ساعة المودم)",
    "seq": 7,
    "step": 3,
    "sub": "pwr",
    "ord": 2,
    "parts": [],
    "match": [r"failed power on modem"],
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "PMIC_RESOUT_L",
    "mtype": "Voltage + Impedance + Signal",
    "flow": "PMIC_RESOUT_L (ريست البيسباند)",
    "seq": 8,
    "step": 3,
    "sub": "rst",
    "ord": 1,
    "parts": [],
    "match": [r"failed power on modem"],
    "fix": "تأكد من المقاومة R1014_E، وإذا كانت سليمة تأكد من سلامة بور البيسباند (U_BBPMU_E). إذا كانا سليمين اعمل Reball للبيسباند.",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "90_PCIE_GP0_AP_TO_BB_C_TX_N",
    "flow13": "90_PCIE_AP_TO_BB_TX_N",
    "dio13": [["TX_P", "OL", "0.333"], ["TX_N", "OL", "0.334"]],
    "fix13": "افصل البوردين: على طبقة البيسباند قراءة OL طبيعية على هالخطين. قيس الممانعة على طبقة المعالج: إذا ما في ممانعة اعمل Reball للمعالج. وتأكد من المكثفين C1007_E وC1008_E.",
    "dio13pm": [["TX_P", "0.353", "OL"], ["TX_N", "0.352", "OL"]],
    "dio13p": [["TX_P", "0.366", "OL"], ["TX_N", "0.371", "OL"]],
    "layer": [["13 Pro · 13 Pro Max", "90_PCIE_GP0_AP_TO_BB_TX_P · 90_PCIE_GP0_AP_TO_BB_TX_N"], ["13", "90_PCIE_GP1_AP_TO_BB_TX_P · 90_PCIE_GP1_AP_TO_BB_TX_N"]],
    "mtype": "Impedance + Signal",
    "parts": [],
    "match": [],
    "match_idle": True,
    "idle_min": 3,
    "fix": "إذا كان على خطوط SMPS فولت: تأكد من المكثفين C2722 وC2723. بعدها افصل البوردين: على طبقة المعالج قراءة OL طبيعية على هالخطين. قيس الممانعة على طبقة البيسباند: إذا ما في ممانعة اعمل Reball للبيسباند.",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "90_PCIE_GP0_AP_TO_BB_TX_P",
    "flow13": "90_PCIE_AP_TO_BB_TX_P",
    "dio13": [["TX_P", "OL", "0.333"], ["TX_N", "OL", "0.334"]],
    "fix13": "افصل البوردين: على طبقة البيسباند قراءة OL طبيعية على هالخطين. قيس الممانعة على طبقة المعالج: إذا ما في ممانعة اعمل Reball للمعالج. وتأكد من المكثفين C1007_E وC1008_E.",
    "dio13pm": [["TX_P", "0.353", "OL"], ["TX_N", "0.352", "OL"]],
    "dio13p": [["TX_P", "0.366", "OL"], ["TX_N", "0.371", "OL"]],
    "layer": [["13 Pro · 13 Pro Max", "90_PCIE_GP0_AP_TO_BB_TX_P · 90_PCIE_GP0_AP_TO_BB_TX_N"], ["13", "90_PCIE_GP1_AP_TO_BB_TX_P · 90_PCIE_GP1_AP_TO_BB_TX_N"]],
    "mtype": "Impedance + Signal",
    "parts": [],
    "match": [],
    "match_idle": True,
    "idle_min": 3,
    "fix": "إذا كان على خطوط SMPS فولت: تأكد من المكثفين C2722 وC2723. بعدها افصل البوردين: على طبقة المعالج قراءة OL طبيعية على هالخطين. قيس الممانعة على طبقة البيسباند: إذا ما في ممانعة اعمل Reball للبيسباند.",
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "SHIELD_PMK_32K_SLEEP_CLK",
    "mtype": "Voltage + Impedance + Signal",
    "parts": [],
    "match": [],
    "match_sleep": True,
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
},
]

def _is_plist_out(txt):
    t = (txt or "")[:4000]
    return ("<plist" in t or t.lstrip().startswith("<?xml")) and "Usage:" not in t[:400]

DIAG_PLANES = ("", "IODeviceTree", "IOPower", "IOService")
DIAG_TYPES = ("All", "WiFi", "GasGauge", "NAND", "HDMI")
_ARG_RX = re.compile(r"^[A-Za-z0-9_,.\- ]{0,120}$")

def diag_run(cmd, arg="", timeout=90):
    """يشغّل أمر idevicediagnostics مسموحاً فقط (قراءة فقط) ويرجع الناتج الخام."""
    arg = (arg or "").strip()
    if cmd not in ("ioreg", "ioregentry", "diagnostics", "mobilegestalt"):
        return {"ok": False, "error": "أمر غير مسموح"}
    if not _ARG_RX.match(arg):
        return {"ok": False, "error": "القيمة تحتوي رموزاً غير مسموحة"}
    if cmd == "ioreg" and arg not in DIAG_PLANES:
        return {"ok": False, "error": "الـ plane غير معروف: " + arg}
    if cmd == "diagnostics" and arg and arg not in DIAG_TYPES:
        return {"ok": False, "error": "النوع غير معروف: " + arg}
    if cmd == "mobilegestalt" and not arg:
        return {"ok": False, "error": "اكتب مفتاحاً واحداً على الأقل"}
    exe = find_tool("idevicediagnostics")
    if not exe:
        return {"ok": False, "error": "الأداة idevicediagnostics غير موجودة على هذا الكمبيوتر (ضعها بجانب ideviceinfo أو في PATH)."}
    args = [exe, cmd] + (arg.split() if arg else [])
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "انتهت المهلة (%d ثانية) بدون رد من الجهاز" % timeout}
    except Exception as e:
        return {"ok": False, "error": "تعذر تشغيل الأداة: " + str(e)}
    out = r.stdout or ""
    err = (r.stderr or "").strip()
    if not out.strip():
        return {"ok": False, "error": "الأداة لم تُرجع بيانات" + ((" — " + err[:300]) if err else "") + " (تأكد أن الهاتف متصل وموثوق به)", "code": r.returncode}
    if "Usage:" in out[:400]:
        return {"ok": False, "error": "الأداة طبعت شاشة المساعدة (الأمر أو الوسيط غير مقبول عندها)", "text": out[:2000]}
    return {"ok": True, "text": out, "stderr": err[:500], "code": r.returncode, "cmdline": " ".join(args[1:])}

def diag_save(label, text):
    d = os.path.join(os.path.expanduser("~"), "NawafTeam_logs")
    try:
        os.makedirs(d, exist_ok=True)
        fn = os.path.join(d, "diag_%s_%s.txt" % (re.sub(r"[^A-Za-z0-9_-]", "_", label)[:30] or "out", time.strftime("%Y%m%d_%H%M%S")))
        with open(fn, "w", encoding="utf-8") as f:
            f.write(text)
        return fn
    except Exception:
        return ""

CH_KEYS = ("IORegistryEntryChildren", "children", "Children")

def diag_tree_matches(txt, rx, limit=200):
    """يمشي على شجرة IORegistry (plist) ويرجع كل عنصر اسمه أو كلاسه يطابق rx، بخصائصه البسيطة كما هي."""
    import plistlib
    try:
        pl = plistlib.loads(txt.encode("utf-8", "ignore"))
    except Exception:
        return None
    root = pl.get("IORegistry", pl) if isinstance(pl, dict) else pl
    res, total = [], [0]
    def short(v):
        if isinstance(v, (bytes, bytearray)):
            return "<%d bytes>" % len(v)
        if isinstance(v, (dict, list)):
            return "<%s %d>" % ("dict" if isinstance(v, dict) else "list", len(v))
        return str(v)[:120]
    def walk(n, path):
        if isinstance(n, list):
            for x in n:
                walk(x, path)
            return
        if not isinstance(n, dict):
            return
        name = str(n.get("IORegistryEntryName", n.get("name", "")))
        cls = str(n.get("IOObjectClass", n.get("className", n.get("class", ""))))
        total[0] += 1
        here = path + [name or cls or "?"]
        if (name or cls) and (rx.search(name) or rx.search(cls)):
            if len(res) < limit:
                props = {k: short(v) for k, v in n.items() if k not in CH_KEYS and k not in ("IORegistryEntryName", "IOObjectClass") and not isinstance(v, (dict, list))}
                props = dict(list(props.items())[:25])
                res.append({"name": name, "class": cls, "path": " / ".join(here[-8:]), "props": props})
        for k in CH_KEYS:
            if k in n:
                walk(n[k], here)
    walk(root, [])
    return {"matches": res, "nodes": total[0]}

# ---- سحب panic تلقائي (يراقب ظهور الجهاز ويسحب كل ما يرجع)
PW = {"on": False, "attempts": 0, "msg": "", "log": [], "thread": None, "devtool": None}

def pw_dest():
    try:
        return crash_dest()
    except Exception:
        return os.path.join(os.path.expanduser("~"), "iphone_crash_reports", "unknown")

def pw_list():
    d = pw_dest()
    out = []
    if os.path.isdir(d):
        for f in find_panics(d)[:20]:
            try:
                out.append({"name": os.path.basename(f), "size": os.path.getsize(f), "mtime": time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(f)))})
            except OSError:
                pass
    return out

def pw_device_present():
    exe = find_tool("idevice_id")
    PW["devtool"] = bool(exe)
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-l"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8)
        return bool(r.stdout.strip())
    except Exception:
        return None

def pw_loop():
    while PW["on"]:
        pres = pw_device_present()
        if pres is False:
            PW["msg"] = "بانتظار ظهور الجهاز…"
            time.sleep(1.5)
            continue
        PW["attempts"] += 1
        PW["msg"] = "جاري السحب (محاولة %d)…" % PW["attempts"]
        ok = False
        try:
            ok, msg, miss = pull_crash_reports(pw_dest())
            PW["log"].append("%s #%d ok=%s %s" % (time.strftime("%H:%M:%S"), PW["attempts"], ok, (msg or "").replace("\n", " | ")[-160:]))
        except Exception as e:
            PW["log"].append("%s #%d خطأ: %s: %s" % (time.strftime("%H:%M:%S"), PW["attempts"], type(e).__name__, e))
        PW["log"] = PW["log"][-30:]
        PW["msg"] = ("آخر محاولة نجحت" if ok else "آخر محاولة فشلت") + "، ملفات panic محلياً: %d" % len(pw_list())
        time.sleep(15 if ok else 3)
    PW["msg"] = "متوقف"

# ---- سحب وتحليل panic تلقائي: بدون أزرار. عند ظهور الجهاز (أو عودته بعد ريستارت) يسحب ويحلل الجديد
AUTO = {"on": True, "state": "بانتظار الجهاز…", "present": False, "last_pull": "", "results": [], "seen": {}, "pulls": 0, "note": "", "msg": "", "missing": False, "prog": {"busy": False, "pct": 0, "stage": ""}}

def _prog(pct, stage, busy=True):
    AUTO["prog"] = {"busy": busy, "pct": int(pct), "stage": stage}

PROG_SECS = 30  # أقل مدة تظهر فيها النتيجة (ثوانٍ)

def auto_pull_once():
    dest = pw_dest()
    AUTO["state"] = "جاري سحب سجلات الريستارت…"
    t0 = time.time()
    stage = ["الاتصال بالجهاز"]
    _stop = threading.Event()
    _prog(0, stage[0])
    def _creep():
        while not _stop.wait(0.4):
            _prog(min(95, 100 * (time.time() - t0) / PROG_SECS), stage[0])
    threading.Thread(target=_creep, daemon=True).start()
    try:
        stage[0] = "سحب التقارير من الهاتف"
        ok, msg, miss = pull_crash_reports(dest)
        stage[0] = "قراءة التقارير"
        AUTO["msg"] = (msg or "")[-500:]
        AUTO["missing"] = bool(miss)
        AUTO["pulls"] += 1
        AUTO["last_pull"] = time.strftime("%H:%M:%S")
        files = find_panics(dest)[:25] if os.path.isdir(dest) else []
        zero = 0
        _batch = []
        stage[0] = "تحليل التقارير ومطابقة الجزء"
        for f in files:
            try:
                key = (os.path.basename(f), os.path.getsize(f), int(os.path.getmtime(f)))
            except OSError:
                continue
            if key in AUTO["seen"]:
                continue
            txt, sz = read_text_any(f)
            if sz == 0:
                zero += 1
                AUTO["seen"][key] = 1
                continue
            AUTO["seen"][key] = 1
            _batch.append({"file": os.path.basename(f), "size": sz, "when": time.strftime("%H:%M:%S"), **panic_json(txt)})
        AUTO["results"] = (_batch + AUTO["results"])[:15]  # الأحدث أولاً
        while time.time() - t0 < PROG_SECS:
            time.sleep(0.3)
        AUTO["note"] = ("وصل %d ملف فارغ (0 بايت) من الهاتف؛ لا شيء لتحليله منه." % zero) if zero else ""
        AUTO["state"] = ("آخر سحب %s — %s" % (AUTO["last_pull"], "تم" if ok else "فشل (تأكد من فتح القفل وTrust)")) + "، تقارير محلّلة: %d" % len(AUTO["results"])
    finally:
        _stop.set()
        time.sleep(0.5)
        _prog(100, "اكتملت المطابقة", busy=False)

def auto_loop():
    was = False
    sched = []
    while True:
        try:
            if not AUTO["on"] or DEMO["on"] or SL["on"] or PW["on"]:
                if AUTO["on"] and (SL["on"] or PW["on"]):
                    AUTO["state"] = "متوقف مؤقتاً أثناء تسجيل اللوغ/المراقبة (حتى لا نزاحم الاتصال)"
                elif not AUTO["on"]:
                    AUTO["state"] = "معطّل"
                time.sleep(2)
                continue
            pres = pw_device_present()
            if pres is None:
                AUTO["state"] = "أداة libimobiledevice غير موجودة على الكمبيوتر"
                time.sleep(5)
                continue
            AUTO["present"] = pres
            if pres and not was:
                sched = [time.time() + 6, time.time() + 40]
                AUTO["state"] = "ظهر الجهاز — سأسحب بعد ثوانٍ…"
            if not pres:
                sched = []
                AUTO["state"] = "بانتظار الجهاز…"
            was = pres
            if sched and time.time() >= sched[0]:
                sched.pop(0)
                auto_pull_once()
        except Exception as e:
            AUTO["state"] = "خطأ: %s" % e
        time.sleep(2)

# ---- قارئ اللوغ المباشر (idevicesyslog) مع حفظ آخر الأسطر عند انقطاع الجهاز
SL = {"on": False, "thread": None, "proc": None, "msg": "", "lines": 0, "c_last": None, "t_last": "", "file": "", "kernel_only": True,
      "sessions": [], "tool": None, "run_dir": ""}
_CNT_RX = re.compile(r"kernel[^<]*<\w+>: (\d{6})\.(\d{6})\b")

# ---- أدلة المودم من اللوغ المباشر (على طريقة NS: قراءة مباشرة ثم تحليل عند الطلب)
MODEM_RX = re.compile(r"baseban|modem|CommCenter|SPMI|BBPMU|\bWTR\b|QLINK|cellular|\bBHI\b|\bQMI\b|IMEI", re.I)
BBL_SEEN = set()
BBL = {"alt": {}, "ev": {}, "total": 0, "since": "", "bt": [], "stat": {}, "rdy": {"f": 0, "t": 0}, "mk": {"pcie": 0, "cc": 0, "mbu": 0, "cpub": 0, "ping": 0, "crash": 0, "qmi": 0, "odr": 0, "linkdown": 0, "offline": 0, "portto": 0, "e51": 0, "lpf": 0, "lbw": 0, "trerr": 0, "fpm": 0}}
BB_RULES_FILE = os.path.join(os.path.expanduser("~"), "NawafTeam_bb_rules.json")

def bbl_norm(line):
    t = re.sub(r"^[A-Z][a-z]{2} +\d+ \d\d:\d\d:\d\d\s+", "", line.strip())
    t = re.sub(r"0x[0-9a-fA-F]+", "0x#", t)
    t = re.sub(r"\d+(?:\.\d+)?", "#", t)
    return t[:200]

BT_RX = re.compile(r"boot state changing\s+([A-Za-z][\w ]*?)\s*->\s*([A-Za-z][\w ]*?)\s*$", re.I)
ALTREASON_RX = re.compile(r'"KeyBasebandResetReason"\s*:\s*"([^"]+)"')
RESETINFO_RX = re.compile(r"Reset requested:\s*ResetInfo\s*\{\s*Type='([^']*)'\s*SubType='([^']*)'.*?Reason='([^']*)'", re.I)
PCIE_RX = re.compile(r"baseband-pcie\]?::(waitFor\w+)\s+timeout[^\n]*", re.I)

def bbl_boot(line, tm):
    """يسجّل تسلسل حالات إقلاع الباسباند بالترتيب، كما كُتبت بالسجل حرفياً (بدون تفسير)."""
    bt = BBL["bt"]
    mr = re.search(r"Baseband is ready:\s*(true|false)", line, re.I)
    if mr:
        BBL["rdy"]["t" if mr.group(1).lower() == "true" else "f"] += 1
    ms = re.search(r'"KeyBaseband(BootTime|EngageTime)"\s*:\s*(\d+)|"KeyBasebandImagePushTime"\s*:\s*(\d+)', line)
    if ms:
        if ms.group(2):
            BBL["stat"]["boot_ms" if ms.group(1) == "BootTime" else "engage_ms"] = int(ms.group(2))
        else:
            BBL["stat"]["push_ms"] = int(ms.group(3))
        return
    if len(bt) >= 400:
        return
    m = BT_RX.search(line)
    if m:
        bt.append({"t": tm, "k": "state", "a": m.group(1), "b": m.group(2)})
        return
    m = ALTREASON_RX.search(line)
    if m:
        BBL["alt"].setdefault(m.group(1)[:160], tm)   # احتياط: سبب الريست من كتلة dump-state لما يغيب سطر ResetInfo
    m = RESETINFO_RX.search(line)
    if m:
        bt.append({"t": tm, "k": "reset", "a": m.group(1) + " / " + m.group(2), "b": m.group(3)[:160]})
        return
    m = PCIE_RX.search(line)
    if m:
        bt.append({"t": tm, "k": "pcie", "a": m.group(1), "b": line.strip()[line.find("::") + 2:][:160]})

# مرجع إقلاع الباسباند لأجهزة سليمة (من فحوصات حقيقية، تُضاف بعد التأكد من سلامة الجهاز)
REF_BOOT = {
    "iPhone14,2": {"name": "iPhone 13 Pro (healthy)", "reached_ready": True, "resets": 0, "boot_ms": 8575, "push_ms": 8034, "engage_ms": 541,
                                "ready_after_s": 11, "src": "healthy scan 14.11.2026, no short"},
}

_LAYER_PID = {"iPhone14,2": "13 Pro · 13 Pro Max", "iPhone14,3": "13 Pro · 13 Pro Max", "iPhone14,5": "13"}

def _layer_for(ly):
    k = _LAYER_PID.get(_cur_pid())
    return [l for l in (ly or []) if l[0] == k] if k else []

_PID_CACHE = {"v": "", "t": 0.0}
def _cur_pid():
    """موديل الجهاز المتصل. يُخزَّن 15 ثانية لأن get_device() يشغّل أدوات خارجية (بطيء على ويندوز) وكان يُنادى عشرات المرات بالتحليل."""
    now = time.time()
    if _PID_CACHE["v"] and now - _PID_CACHE["t"] < 15:
        return _PID_CACHE["v"]
    p = ""
    try:
        p = (get_device().get("fields") or {}).get("Model Identifier", "") or ""
    except Exception:
        p = ""
    if p and p != "—":
        _PID_CACHE["v"] = p
    _PID_CACHE["t"] = now
    return _PID_CACHE["v"]

HW_IMGS = {}
HW_POINTS = {}
HW_LDO = {
    "PP_1V22_SMPS2": [1, 2, 4, 12, 15, 18],
    "PP_0V82_SMPS3": [3, 7, 8, 9, 17, 19],
    "PP_1V9_SMPS4": [5, 6, 16],
    "PP_0V75_SMPS7": [14],
}
for _c in HW_CASES:
    if _c.get("net") in HW_LDO:
        _c["ldo"] = ["LDO%d" % _n for _n in HW_LDO[_c["net"]]]
SHOW_HW_POINTS = False  # نقاط الفحص والصور مخفية: أسماء الخطوط فقط
HW_CASES.append({
    "model": "iPhone 16 Pro Max",
    "pid": "iPhone17,2",
    "net": "BB_EEPROM",
    "mtype": "Signal",
    "parts": ["U_EEPROM_E", "R1118_E", "R1121_E"],
    "match": [r"failed connecting to sahara"],
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
})

HW_CASES.append({
    "model": "iPhone 16 Pro Max",
    "pid": "iPhone17,2",
    "net": 'GPIO_SEQ_PMU_TO_BBPMU_RESET_L',
    "mtype": "Signal",
    "parts": ['R612_E'],
    "match": [r"failed power on modem"],
    "title": "",
    "symptoms": "",
    "checks": "",
    "result": "",
    "verified": "",
})

HW_CASES.append({
    "model": "iPhone 16 Pro Max",
    "pid": "iPhone17,2",
    "net": 'BB_BBPMU_SPMI_DATA',
    "mtype": "Signal",
    "parts": ['R607_E'],
    "match": [r"failed power on modem"],
    "title": "",
    "symptoms": "",
    "checks": "",
    "result": "",
    "verified": "",
})

HW_CASES.append({
    "model": "iPhone 16 Pro Max",
    "pid": "iPhone17,2",
    "net": 'BB_BBPMU_SPMI_CLK',
    "mtype": "Signal",
    "parts": ['R608_E'],
    "match": [r"failed power on modem"],
    "title": "",
    "symptoms": "",
    "checks": "",
    "result": "",
    "verified": "",
})

HW_CASES.append({
    "model": "iPhone 16 Pro Max",
    "pid": "iPhone17,2",
    "net": 'Y_XTAL_E',
    "mtype": "Signal",
    "parts": ['Y_XTAL_E'],
    "match": [r"failed power on modem"],
    "title": "",
    "symptoms": "",
    "checks": "",
    "result": "",
    "verified": "",
})

HW_CASES.append({
    "model": "iPhone 16 Pro Max",
    "pid": "iPhone17,2",
    "net": 'PCIE_GP1_AP_TO_BB_PERST_L',
    "mtype": "Signal",
    "parts": ['R2821'],
    "match": [r"failed power on modem"],
    "title": "",
    "symptoms": "",
    "checks": "",
    "result": "",
    "verified": "",
})

HW_CASES.append({
    "model": "iPhone 16 Pro Max",
    "pid": "iPhone17,2",
    "net": "GPIO_NUB_FROM_BB_RESET_DETECT_L",
    "mtype": "Signal",
    "seq": 1,
    "flow": "GPIO_NUB_FROM_BB_RESET_DETECT_L",
    "fixbad": "",
    "flowend": "إذا كل الخطوط سليمة ولسا المشكلة موجودة: Reball Baseband، ثم Replace Transceiver.",
    "parts": ["PP1398_E"],
    "match": [r"baseband crash detected"],
    "title": "",
    "symptoms": (""),
    "checks": "",
    "result": "",
    "verified": "",
})

HW_CASES.append({
    "model": "iPhone 16 Pro Max",
    "pid": "iPhone17,2",
    "net": 'QLINK_REQ_SDR0_TO_BB',
    "mtype": "Signal",
    "seq": 7,
    "flow": "QLINK_REQ_SDR0_TO_BB",
    "fixbad": "",
    "flowend": "إذا كل الخطوط سليمة ولسا المشكلة موجودة: Reball Baseband، ثم Replace Transceiver.",
    "parts": ['R1501_E', 'PP1351_E'],
    "match": [r"baseband crash detected"],
    "title": "",
    "symptoms": "",
    "checks": "",
    "result": "",
    "verified": "",
})

HW_CASES.append({
    "model": "iPhone 16 Pro Max",
    "pid": "iPhone17,2",
    "net": 'DVDD_BYP',
    "mtype": "Voltage",
    "parts": [],
    "title": "",
    "symptoms": "",
    "checks": "",
    "result": "",
    "verified": "",
})

HW_CASES.append({
    "model": "iPhone 16 Pro Max",
    "pid": "iPhone17,2",
    "net": 'VDD_IO',
    "mtype": "Voltage",
    "parts": [],
    "title": "",
    "symptoms": "",
    "checks": "",
    "result": "",
    "verified": "",
})


def _sdr16(net, seq, parts, title, symptoms, result, verified):
    HW_CASES.append({
        "model": "iPhone 16 Pro Max",
        "pid": "iPhone17,2",
        "net": net,
        "mtype": "Voltage" if net.startswith("PP_") else "Signal",
        "seq": seq,
        "flow": net,
        "fixbad": "",
        "flowend": "إذا كل الخطوط سليمة ولسا المشكلة موجودة: Reball Baseband، ثم Replace Transceiver.",
        "parts": parts,
        "match": [r"baseband crash detected"],
        "title": "",
        "symptoms": "",
        "checks": "",
        "result": "",
        "verified": "",
    })

_PAT = ""
_NOTE = ""

_sdr16("PP_0V752_SMPS1_6", 2, ["U_BB_E", "U_BBPMU_E", "PP1359_E", "L306_E", "L301_E", "C739_E", "C737_E", "C733_E", "C731_E", "C730_E"],
    "",
    "",
    "",
    "")

_sdr16("PP_0V752_SMPS5", 3, ["U_BB_E", "U_BBPMU_E", "L305_E", "C833_E", "C826_E", "C820_E", "C815_E", "C810_E", "C801_E", "C313_E"],
    "",
    "",
    "",
    "")

_sdr16("SHIELD_SDR0_76P8M_CLK", 4, ["U_XCVR0_E", "UBBPMK_E", "PP1320_E"],
    "",
    "",
    "",
    "")

_sdr16("SHIELD_BB_TO_SDR0_WMSS_RESET_L", 5, [],
    "",
    "",
    "",
    "")

_sdr16("QLINK_EN_BB_TO_SDR0", 6, [],
    "",
    "",
    "",
    "")


_END_B = "إذا كل الخطوط سليمة: Replace BBPMU."
def _bbp16(net, seq, mtype, title, symptoms, verified):
    HW_CASES.append({
        "model": "iPhone 16 Pro Max",
        "pid": "iPhone17,2",
        "net": net,
        "mtype": mtype,
        "seq": seq,
        "flow": net,
        "fixbad": "",
        "flowend": _END_B,
        "parts": [],
        "match": [],
        "match_idle": True,
        "idle_min": 3,
        "title": "",
        "symptoms": "",
        "checks": "",
        "result": "",
        "verified": "",
    })

_SYM_B = ""
_bbp16("PMIC_RESOUT_L", 1, "Voltage", "", "", "")
_bbp16("PMU_TO_BBPMU_RESET_L", 2, "Voltage", "", "", "")
_bbp16("PP_3V1_LDO10", 3, "Voltage + Impedance", "", "", "")
_bbp16("LDO BBPMU", 4, "Impedance", "", "", "")
_bbp16("SMPS BBPMU", 5, "Impedance", "", "", "")

if SHOW_HW_POINTS:
    for _c in HW_CASES:
        if _c.get("net") in HW_POINTS:
            _c["point"] = HW_POINTS[_c["net"]]
        if _c.get("net") in HW_IMGS:
            _c["img"] = "data:image/jpeg;base64," + HW_IMGS[_c["net"]]


# خطوات فحص من جدول الإنتربوزر (مش حالات مجرّبة، فما بتظهر بمكتبة الأعطال)
HW_FLOW_ONLY = [
{
    "model": "iPhone 13 · 13 mini · 13 Pro · 13 Pro Max",
    "net": "PP_VDD_MAIN_BBPMU",
    "dio13": [["", "0.378", "0.318"]],
    "dio13pm": [["", "0.323", "0.335"]],
    "mtype": "Voltage + Impedance",
    "flow": "PP_VDD_MAIN_BBPMU",
    "seq": 0.5,
    "step": 1,
    "sub": "pwr",
    "ord": 0,
    "dio13p": [["", "0.339", "0.339"]],
    "layer": [["13", "PP_VDD_MAIN_BBPMU_1 · PP_VDD_MAIN_BBPMU_2 · PP_VDD_MAIN_BBPMU_3 · PP_VDD_MAIN_BBPMU_4"]],
    "parts": [],
    "match": [r"failed power on modem"],
    "fix": "إذا ما في فولط على طبقة البيسباند: افصل البوردين وتأكد من نقاط PP_VDD_MAIN_BBPMU على الإنتربوزر.",
    "title": "",
},
]

def hw_match(reasons, timeline, rdy=None, resets=0, states=0, mk=None):
    """يطابق نص أسباب الريست/أسطر PCIe بالسكان مع حالات مكتبة الأعطال (حالات المستخدم نفسه فقط) ويرجع أسماء الخطوط المسجّلة."""
    txt = " \n".join([r["text"] for r in reasons] + [str(e.get("b", "")) + " " + str(e.get("a", "")) for e in timeline if e.get("k") == "pcie"])
    out = []
    for c in HW_CASES + HW_FLOW_ONLY:
        if c.get("pid") and c["pid"] != _cur_pid():
            continue
        if not c.get("pid") and _cur_pid() and _cur_pid() not in ("iPhone14,2", "iPhone14,3", "iPhone14,4", "iPhone14,5"):
            continue
        pats = c.get("match") or []
        hit = [x for x in pats if re.search(x, txt, re.I)]
        if c.get("match_pcie") and mk and ((mk.get("pcie", 0) >= c["match_pcie"] and not mk.get("mbu", 0) and (mk.get("crash", 0) >= 1 or re.search(r"Baseband Crash", txt, re.I))) or mk.get("portto", 0) >= 1) and not (rdy or {}).get("t", 0) and not re.search(r"failed power on modem|TransportError|Transport Error|kABPLinkBWNotification|kABPEnterLowPowerFailed", txt, re.I):
            hit.append("baseband-pcie-power-state")
        if c.get("match_sleep") and mk and rdy and mk.get("cpub", 0) >= 10 and rdy.get("f", 0) >= 10 and rdy.get("t", 0) == 0 and not mk.get("mbu", 0) and not mk.get("pcie", 0):
            hit.append("pcie-children-up-ready:false")
        if c.get("match_crash") and mk and rdy and (re.search(r"Baseband Crash", txt, re.I) or mk.get("crash", 0) >= 1) and rdy.get("t", 0) >= 1 and mk.get("ping", 0) >= 1 and not mk.get("mbu", 0):
            hit.append("baseband-crash-after-ready")
        if c.get("match_odr") and mk:
            if mk.get("odr", 0) >= 1 or re.search(r"output did not return done", txt, re.I):
                if (c["match_odr"] == "link") == (mk.get("linkdown", 0) >= 1):
                    hit.append("output-did-not-return-done/" + c["match_odr"])
        if c.get("match_idle") and rdy and rdy.get("f", 0) >= c.get("idle_min", 1) and rdy.get("t", 0) == 0 and resets == 0 and states == 0 and not (mk or {}).get("qmi", 0):
            hit.append("ready:false-no-boot-lines")
        if hit and c.get("net"):
            out.append({"net": c["net"], "parts": c.get("parts") or [], "title": "", "model": c["model"], "ldo": c.get("ldo") or [], "fix": ({"iPhone14,5": c.get("fix13"), "iPhone14,2": c.get("fix13p"), "iPhone14,3": c.get("fix13pm")}.get(_cur_pid()) or c.get("fix", "")), "step": c.get("step", 0), "when": c.get("when", ""), "ord": c.get("ord", 0), "sub": c.get("sub", ""), "seq": c.get("seq", 0), "flow": (c.get("flow13") if _cur_pid() == "iPhone14,5" and c.get("flow13") else c.get("flow", "")), "fixbad": c.get("fixbad"), "flowend": c.get("flowend", ""), "branch": c.get("branch"), "branchend": c.get("branchend", ""), "gone": c.get("gone", ""), "mtype": c.get("mtype", ""), "layer": _layer_for(c.get("layer")), "dio": {"iPhone14,2": c.get("dio13p"), "iPhone14,3": c.get("dio13pm"), "iPhone14,5": c.get("dio13")}.get(_cur_pid()) or [], "hit": hit})
    return out

def bbl_boot_view():
    bt = list(BBL["bt"])
    states = [e for e in bt if e["k"] == "state"]
    resets = [e for e in bt if e["k"] == "reset"]
    if not resets and BBL["alt"]:
        resets = [{"t": t, "k": "reset", "a": "HardReset / Boot Failure" if "boot up failure" in r else "Reset", "b": r} for r, t in BBL["alt"].items()]
    reasons = {}
    for e in resets:
        reasons[e["a"] + " | " + e["b"]] = reasons.get(e["a"] + " | " + e["b"], 0) + 1
    return {"timeline": bt[:120], "states": len(states), "reached_ready": any("isready" in (e["b"].lower(), e["a"].lower()) for e in states),
            "resets": len(resets), "reasons": [{"text": k, "n": v} for k, v in sorted(reasons.items(), key=lambda x: -x[1])],
            "to_resetting": sum(1 for e in states if e["b"].lower() == "resetting"), "stat": dict(BBL["stat"]),
            "ref": refs_boot_get(_cur_pid()),
            "hw": hw_match([{"text": k} for k in reasons], bt, dict(BBL["rdy"]), len(resets), len(states), dict(BBL["mk"])),
            "partial": _bbl_partial(len(resets), len(states)),
            "stage": _bbl_stage(" \n".join(reasons), any("isready" in (e["b"].lower(), e["a"].lower()) for e in states), len(resets))}

BB_STAGES = ["Baseband PMU & Power", "PCIe Link (AP ↔ Baseband)", "Baseband (Firmware Boot)", "Baseband EEPROM", "SDR (Transceiver)"]
def _bbl_stage(txt, reached_ready, nres):
    """وين وقف البيسباند بالإقلاع، من علامات السكان المجرّبة. stop=-1 ما ظهر عطل، None ما في معلومة كافية."""
    mk, rdy = BBL["mk"], BBL["rdy"]
    if _bbl_partial(nres, 0) and not nres:
        return {"stages": BB_STAGES, "stop": None}
    ready = reached_ready or rdy.get("t", 0) > 0
    if mk.get("e51", 0):
        st = 3
    elif mk.get("odr", 0):
        st = 2
    elif mk.get("fpm", 0) or re.search(r"failed power on modem", txt, re.I):
        st = 0
    elif mk.get("portto", 0) or mk.get("lpf", 0) or mk.get("lbw", 0) or mk.get("trerr", 0) or re.search(r"TransportError|Transport Error", txt):
        st = 1
    elif mk.get("crash", 0) and ready:
        st = 4
    elif mk.get("crash", 0) or (rdy.get("f", 0) and not mk.get("qmi", 0)):
        st = -2   # ما وصل لجاهز بدون علامة تحدد القطعة
    elif mk.get("qmi", 0) and not nres:
        st = -1
    else:
        st = None
    return {"stages": BB_STAGES, "stop": st}

def _bbl_partial(resets, states):
    """سكان ناقص: الباسباند لا يعمل (لا QMI، وready:false أو غير أونلاين) ولم يُلتقط أي سبب حالي (لا ريست ولا حالات إقلاع ولا Crash/Boot failure)."""
    mk = BBL["mk"]
    captured = resets > 0 or states > 0 or mk.get("crash", 0) or mk.get("odr", 0) or mk.get("linkdown", 0) or mk.get("mbu", 0) or mk.get("portto", 0)
    dead = not mk.get("qmi", 0) and not (mk.get("cpub", 0) >= 10 and not mk.get("pcie", 0)) and (BBL["rdy"].get("f", 0) > 0 or mk.get("offline", 0) > 0)
    return bool(dead and not captured)

def bbl_mk_extra(line):
    """يعدّ سطور kernel التي لا تمر بفلتر MODEM_RX: قنوات PCIe للمودم (CCLogStream / CCPipe) ظهرت = رابط PCIe اشتغل."""
    if "[childPublished()]" in line and ("CCLogStream" in line or "CCPipe" in line) and line not in BBL_SEEN:
        BBL_SEEN.add(line)
        BBL["mk"]["cpub"] += 1
    if "output did not return done" in line and ("KeyBasebandResetReason" in line or "Reset requested" in line):
        BBL["mk"]["odr"] += 1           # سبب الإقلاع output did not return done (من سطر الريست أو من KeyBasebandResetReason)
    if "BBUpdater error 51" in line or "kBBUReturnFailCreateTransportSecond" in line:
        BBL["mk"]["e51"] += 1           # البيسباند فشل يقرا الإيبروم
    if "kABPEnterLowPowerFailed" in line and "kKeyTraceDumpStateReason" not in line:
        BBL["mk"]["lpf"] += 1
    if "kABPLinkBWNotification" in line and "kKeyTraceDumpStateReason" not in line:
        BBL["mk"]["lbw"] += 1
    if "failed power on modem" in line and "kKeyTraceDumpStateReason" not in line and "kKeyAWDTraceRequestReason" not in line:
        BBL["mk"]["fpm"] += 1           # البيسباند ما اشتغل أصلاً
    if "SubType='TransportError'" in line:
        BBL["mk"]["trerr"] += 1
    if "baseband-pcie" in line and "timeout waiting for port to enable" in line:
        BBL["mk"]["portto"] += 1        # رابط PCIe الباسباند لم يشتغل أصلاً (المنفذ لم يتفعّل)
    if "online(false)" in line and "baseband" in line.lower():
        BBL["mk"]["offline"] += 1       # الباسباند غير أونلاين
    if "pciErrorHandler: pci link error" in line or ("baseband-pcie" in line and "Unexpected link down" in line):
        BBL["mk"]["linkdown"] += 1      # انقطاع رابط PCIe الباسباند
    if "Baseband Crash" in line and ("KeyBasebandResetSubType" in line or "SubType='Baseband Crash'" in line):
        BBL["mk"]["crash"] += 1         # نوع الريست Baseband Crash (من سطر Reset requested أو من سطر KeyBasebandResetSubType)

def bbl_add(line):
    BBL["total"] += 1
    if "baseband-pcie" in line:
        BBL["mk"]["pcie"] += 1          # kernel: منفذ PCIe الباسباند يبدّل حالة الطاقة (يحاول يبني الرابط)
    if "Started with name" in line and "CommCenter" in line:
        BBL["mk"]["cc"] += 1            # CommCenter يعيد تسجيل نفسه مراراً
    if "modem boot up failure" in line and "kKeyTraceDumpStateReason" not in line and "kKeyAWDTraceRequestReason" not in line:
        BBL["mk"]["mbu"] += 1           # لا نعدّ أسباب تقارير قديمة محفوظة (Trace Dump)
    if "QMI: Svc" in line:
        BBL["mk"]["qmi"] += 1           # الباسباند يرد بخدمات QMI
    if re.search(r"ping failure count: [1-9]", line):
        BBL["mk"]["ping"] += 1          # CommCenter: الباسباند لا يرد على ping
    k = bbl_norm(line)
    e = BBL["ev"].get(k)
    mt = re.match(r"^[A-Z][a-z]{2} +\d+ (\d\d:\d\d:\d\d)", line)
    tm = mt.group(1) if mt else ""
    try:
        bbl_boot(line, tm)
    except Exception:
        pass
    if e is None:
        if len(BBL["ev"]) >= 600:
            return
        BBL["ev"][k] = {"n": 1, "first": tm, "last": tm, "sample": line.strip()[:300]}
    else:
        e["n"] += 1
        if tm:
            e["last"] = tm

def bb_rules_load():
    try:
        with open(BB_RULES_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return [r for r in d if isinstance(r, dict) and r.get("pattern")]
    except Exception:
        return []

def bbl_analyze():
    rules = bb_rules_load()
    rows = sorted(BBL["ev"].values(), key=lambda e: -e["n"])
    out_rules = []
    for r in rules:
        try:
            rx = re.compile(r["pattern"], re.I)
        except re.error:
            continue
        hits = [e for e in rows if rx.search(e["sample"])]
        if hits:
            out_rules.append({"pattern": r["pattern"], "meaning": r.get("meaning", ""), "source": r.get("source", ""), "count": sum(e["n"] for e in hits), "first": min(e["first"] for e in hits if e["first"]) if any(e["first"] for e in hits) else "", "last": max(e["last"] for e in hits if e["last"]) if any(e["last"] for e in hits) else ""})
    return {"ok": True, "total": BBL["total"], "kinds": len(rows), "since": BBL["since"], "rows": rows[:60], "rules": out_rules, "rules_file": BB_RULES_FILE, "rules_count": len(rules), "baseline": cmp_baseline(), "boot": bbl_boot_view()}

# ---- قاعدة الأجهزة السليمة (ثابتة داخل البرنامج، مأخوذة من سجلات أجهزة سليمة حقيقية) + مقارنة تلقائية
BASELINES_ENC = "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"
_BASE = {}

def base_get(pt):
    ub = refs_bb_get(pt) if pt else None
    if not _BASE and _DK["k"]:
        try:
            import zlib
            _BASE.update(json.loads(zlib.decompress(_dec(BASELINES_ENC, _DK["k"])).decode("utf-8")))
        except Exception:
            _BASE["_err"] = {}
    b0 = _BASE.get(pt)
    if ub and b0:    # مرجع المستخدم + المرجع المدمج: اتحاد أسطر السليم
        mod = dict(b0["modem"])
        for k, v in ub["modem"].items():
            mod[k] = max(mod.get(k, 0), v)
        return {"name": ub["name"], "modem": mod, "err": sorted(set(b0["err"]) | set(ub["err"])), "lines": b0.get("lines", 0) + ub.get("lines", 0)}
    return ub or b0

CMP_LINE = re.compile(r"^[A-Z][a-z]{2} +\d+ \d\d:\d\d:\d\d(?:\.\d+)? ([^\[\s(]+)(?:\([^)]*\))?\[\d+\] <(\w+)>: ")

# ---------------------------------------------------------------- مكتبة المراجع (مجلدات يضيف فيها المستخدم فحوصات/لوغات هواتف سليمة)
REFS_DIR = os.path.join(os.path.expanduser("~"), "NawafTeam_refs")
REF_EXTRA = {"iPhone18,3": "iPhone 17"}
_REFC = {}

def refs_names():
    d = dict(PRODUCT_NAMES)
    d.update(REF_EXTRA)
    return d

def refs_init():
    """ينشئ مجلداً لكل موديل داخل Baseband و Flash. يضع المستخدم داخل مجلد الموديل فحصاً/لوغاً لجهاز سليم فقط."""
    try:
        txt = {"Baseband": "ضع هنا مجلد فحص الباسباند (syslog_...) لجهاز سليم، داخل مجلد موديله.\nPut a folder of a HEALTHY phone baseband scan (syslog_... with session_*.log files) inside its model folder.\n",
               "Flash": "ضع هنا ملف لوغ 3uTools (txt) لفلاش ناجح انتهى بجهاز سليم، داخل مجلد موديله.\nPut a 3uTools log (.txt) of a SUCCESSFUL flash of a healthy phone inside its model folder.\n"}
        for kind, t in txt.items():
            os.makedirs(os.path.join(REFS_DIR, kind), exist_ok=True)
            for pid, nm in sorted(refs_names().items()):
                os.makedirs(os.path.join(REFS_DIR, kind, pid + " - " + nm), exist_ok=True)
            rp = os.path.join(REFS_DIR, kind, "README.txt")
            if not os.path.exists(rp):
                with open(rp, "w", encoding="utf-8") as f:
                    f.write(t)
    except Exception:
        pass

def _ref_dir(kind, pid):
    base = os.path.join(REFS_DIR, kind)
    try:
        for n in os.listdir(base):
            if n == pid or n.startswith(pid + " -"):
                p = os.path.join(base, n)
                if os.path.isdir(p):
                    return p
    except OSError:
        pass
    return ""

def _ref_files(d, exts):
    out = []
    for root, _dirs, files in os.walk(d):
        for fn in files:
            if fn.lower().endswith(exts) and not fn.lower().startswith(("tail_", "readme")):
                p = os.path.join(root, fn)
                try:
                    st = os.stat(p)
                    out.append((p, st.st_mtime, st.st_size))
                except OSError:
                    pass
    return sorted(out)

def _ref_cached(key, files, builder):
    sig = tuple(files)
    c = _REFC.get(key)
    if c and c[0] == sig:
        return c[1]
    try:
        v = builder([f[0] for f in files])
    except Exception:
        v = None
    _REFC[key] = (sig, v)
    return v

def _build_bb_ref(paths, pid):
    modem, err, lines = {}, set(), 0
    boot = {"reached_ready": False, "resets": 0, "boot_ms": None, "push_ms": None, "engage_ms": None}
    caps = 0
    for fp in paths:
        cm, ce, st, rs, ready = {}, {}, 0, 0, False
        with open(fp, encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.rstrip("\n")
                mm = CMP_LINE.match(ln)
                if mm:
                    lines += 1
                    lvl = mm.group(2)
                    if lvl in ("Error", "Fault"):
                        ce[cmp_norm(ln)] = 1
                    elif lvl != "Debug" and MODEM_RX.search(ln):
                        k = cmp_norm(ln)
                        cm[k] = cm.get(k, 0) + 1
                if "aseban" in ln or "ResetInfo" in ln:
                    m1 = BT_RX.search(ln)
                    if m1 and m1.group(2).lower() == "isready":
                        ready = True
                    if m1 and m1.group(2).lower() == "resetting":
                        rs += 1
                    ms = re.search(r'"KeyBaseband(BootTime|EngageTime)"\s*:\s*(\d+)|"KeyBasebandImagePushTime"\s*:\s*(\d+)', ln)
                    if ms:
                        if ms.group(2):
                            boot["boot_ms" if ms.group(1) == "BootTime" else "engage_ms"] = int(ms.group(2))
                        else:
                            boot["push_ms"] = int(ms.group(3))
        if cm or ce:
            caps += 1
        for k, v in cm.items():
            modem[k] = max(modem.get(k, 0), v)
        err.update(ce)
        boot["reached_ready"] = boot["reached_ready"] or ready
        boot["resets"] = max(boot["resets"], rs)
    if not modem:
        return None
    nm = refs_names().get(pid, pid)
    return {"name": "%s (healthy, your folder, %d capture%s)" % (nm, caps, "" if caps == 1 else "s"),
            "modem": modem, "err": sorted(err), "lines": lines, "boot": boot}

def refs_bb_get(pid):
    d = _ref_dir("Baseband", pid)
    if not d:
        return None
    files = _ref_files(d, (".log",))
    if not files:
        return None
    return _ref_cached(("bb", pid), files, lambda ps: _build_bb_ref(ps, pid))

def _build_fl_ref(paths, pid):
    best = None
    for fp in sorted(paths, key=lambda p: os.path.getmtime(p)):
        M = Monitor()
        with open(fp, encoding="utf-8", errors="replace") as f:
            for ln in f:
                M.feed(ln.rstrip("\n"))
        sn = M.snapshot(0)
        if not (sn.get("success") or sn.get("end_reached")) or not sn.get("timeline"):
            continue
        def _s(t):
            h, mi, x = t.split(":")
            return int(h) * 3600 + int(mi) * 60 + float(x)
        tl = sn["timeline"]
        t0 = min(_s(e["start"]) for e in tl)
        st = {}
        for e in tl:
            if e["name"] not in st:
                st[e["name"]] = [round(_s(e["start"]) - t0, 1), (round(_s(e["end"]) - _s(e["start"]), 1) if e["end"] else None)]
        nm = refs_names().get(pid, pid)
        best = {"name": nm + " (healthy flash, your folder)", "total": sn.get("took") or "", "src": os.path.basename(fp), "stages": st}
    return best

def refs_flash_get(pid):
    if not pid:
        return None
    d = _ref_dir("Flash", pid)
    if d:
        files = _ref_files(d, (".txt", ".log"))
        if files:
            r = _ref_cached(("fl", pid), files, lambda ps: _build_fl_ref(ps, pid))
            if r:
                return r
    return REF_FLASH.get(pid)

def refs_boot_get(pid):
    b = refs_bb_get(pid) if pid else None
    if b and b.get("boot") and (b["boot"]["reached_ready"]) and b["boot"]["resets"] == 0:
        bt = b["boot"]
        return {"name": b["name"], "reached_ready": True, "resets": 0, "boot_ms": bt["boot_ms"], "push_ms": bt["push_ms"], "src": "your reference folder"}
    return REF_BOOT.get(pid)

CMPL = {"mod": {}, "err": {}}

def cmp_norm(line):
    t = re.sub(r"^[A-Z][a-z]{2} +\d+ \d\d:\d\d:\d\d(?:\.\d+)?\s+", "", line.strip())
    t = re.sub(r"\[\d+\]", "[#]", t)
    t = re.sub(r"0x[0-9a-fA-F]+", "0x#", t)
    t = re.sub(r"\d+(?:\.\d+)?", "#", t)
    return t[:200]

def cmpl_add(line):
    m = CMP_LINE.match(line)
    if not m:
        return
    lvl = m.group(2)
    if lvl in ("Error", "Fault"):
        d = CMPL["err"]
    elif lvl != "Debug" and MODEM_RX.search(line):
        d = CMPL["mod"]
    else:
        return
    k = cmp_norm(line)
    e = d.get(k)
    if e is None:
        if len(d) < 3000:
            d[k] = [1, line.strip()[:300]]
    else:
        e[0] += 1

def cmpl_reset():
    CMPL["mod"].clear(); CMPL["err"].clear()

def cmp_baseline():
    try:
        pt = (get_device().get("fields") or {}).get("Model Identifier", "")
    except Exception:
        pt = ""
    b = base_get(pt) if pt and pt != "—" else None
    if not b:
        return {"available": False, "model": pt, "refs_dir": os.path.join(REFS_DIR, "Baseband")}
    bm, be = b["modem"], set(b["err"])
    # سطر الخطأ المرتبط بالمودم (مثل CommCenter <Error>) يُخزَّن بقائمة الأخطاء عند السحب الحي، لكن المرجع يحسبه ضمن أسطر المودم؛
    # لذلك نعتبر الأسطر الظاهرة = أسطر المودم + الأخطاء، وإلا يظهر سطر موجود فعلاً كأنه «غائب».
    seen = dict(CMPL["err"]); seen.update(CMPL["mod"])
    missing = sorted(((v, k) for k, v in bm.items() if k not in seen), reverse=True)
    # قائمة «أخطاء لم تظهر عند السليم»: نبقي فقط ما له علاقة بالمودم (ضجيج النظام مثل Siri وMobileAsset يُستبعد)
    new_err = sorted(((e[0], e[1]) for k, e in CMPL["err"].items() if k not in be and MODEM_RX.search(e[1])), reverse=True)
    return {"available": True, "model": pt, "name": b["name"], "base_modem_kinds": len(bm),
            "seen_of_base": len(bm) - len(missing), "live_modem_kinds": len(seen),
            "missing": [{"n": v, "line": k} for v, k in missing[:30]], "missing_total": len(missing),
            "new_err": [{"n": v, "line": k} for v, k in new_err[:30]], "new_err_total": len(new_err)}

BBF = {"state": "", "armed": False, "n": 0, "files": []}

def bbf_run():
    """بعد السكان: يسحب تقارير الجهاز ويعرض أي ملف اسمه أو مساره فيه baseband/coredump/BBU. لا يُعرض شيء إن لم يوجد."""
    BBF.update(state="run", n=0, files=[])
    try:
        dest = crash_dest()
        pull_crash_reports(dest)
        hits = []
        for root, _d, fs in os.walk(dest):
            for f in fs:
                p = os.path.join(root, f)
                if re.search(r"baseband|coredump|bbu", p[len(dest):], re.I):
                    try:
                        if os.path.getsize(p) > 0:
                            hits.append((os.path.getmtime(p), os.path.relpath(p, dest)))
                    except OSError:
                        pass
        hits.sort(reverse=True)
        BBF.update(n=len(hits), files=[{"f": h[1], "t": time.strftime("%Y-%m-%d %H:%M", time.localtime(h[0]))} for h in hits[:10]], dest=dest)
    except Exception:
        pass
    BBF["state"] = "done"

def sl_dir():
    return os.path.join(os.path.expanduser("~"), "NawafTeam_logs")

def sl_loop():
    from collections import deque
    SL["run_dir"] = os.path.join(sl_dir(), "syslog_" + time.strftime("%Y%m%d_%H%M%S"))
    try:
        os.makedirs(SL["run_dir"], exist_ok=True)
    except Exception as e:
        SL["msg"] = "تعذر إنشاء المجلد: %s" % e
        SL["on"] = False
        return
    n_sess = 0
    while SL["on"]:
        exe = find_tool("idevicesyslog")
        SL["tool"] = bool(exe)
        if not exe:
            SL["msg"] = "أداة idevicesyslog غير موجودة بمجلد الأدوات."
            SL["on"] = False
            break
        idt = find_tool("idevice_id")
        if idt:
            try:
                r = subprocess.run([idt, "-l"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
                if not r.stdout.strip():
                    SL["msg"] = "بانتظار ظهور الجهاز…"
                    time.sleep(0.3)
                    continue
            except Exception:
                pass
        try:
            p = subprocess.Popen([exe], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        except Exception as e:
            SL["msg"] = "تعذر تشغيل الأداة: %s" % e
            time.sleep(2)
            continue
        SL["proc"] = p
        SL["last_line"] = time.monotonic()
        def _wd(pp=p):
            # مراقب: إذا توقف وصول الأسطر أكثر من 12 ثانية يُنهي الأداة ليعيد الاتصال تلقائياً
            while pp.poll() is None and SL["on"]:
                time.sleep(1)
                if time.monotonic() - SL["last_line"] > 12:
                    try:
                        pp.terminate()
                    except Exception:
                        pass
                    break
        threading.Thread(target=_wd, daemon=True).start()
        seg = None  # مقطع حالي: dict
        def seg_open():
            nonlocal n_sess
            n_sess += 1
            fpath = os.path.join(SL["run_dir"], "session_%02d.log" % n_sess)
            s = {"n": n_sess, "start": time.strftime("%H:%M:%S"), "ring": deque(maxlen=300), "total": 0, "lines": 0,
                 "c_first": None, "c_last": None, "t_last": "", "fpath": fpath, "fh": open(fpath, "w", encoding="utf-8", errors="replace")}
            SL.update({"lines": 0, "c_last": None, "t_last": "", "file": fpath, "msg": "متصل، عم يقرأ… (مقطع %d)" % n_sess})
            return s
        def seg_close(s, reason):
            try:
                s["fh"].close()
            except Exception:
                pass
            tail_path = os.path.join(SL["run_dir"], "tail_%02d.txt" % s["n"])
            try:
                with open(tail_path, "w", encoding="utf-8", errors="replace") as th:
                    th.write("# مقطع %d | بداية %s | سبب الإغلاق: %s | آخر سطر بساعة الجهاز %s | آخر عداد kernel %s | أسطر محفوظة %d\n" % (s["n"], s["start"], reason, s["t_last"], ("%.3f" % s["c_last"]) if s["c_last"] is not None else "—", s["lines"]))
                    th.write("\n".join(s["ring"]) + "\n")
            except Exception:
                tail_path = ""
            SL["sessions"].append({"n": s["n"], "start": s["start"], "end": time.strftime("%H:%M:%S"), "dev_last": s["t_last"], "c_first": s["c_first"], "c_last": s["c_last"], "lines": s["lines"], "read": s["total"], "reason": reason, "file": os.path.basename(s["fpath"]), "tail": os.path.basename(tail_path)})
            SL["sessions"] = SL["sessions"][-40:]
        last_arr = time.monotonic()
        try:
            for raw in iter(p.stdout.readline, b""):
                if not SL["on"]:
                    break
                now = time.monotonic()
                SL["last_line"] = now
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                m = _CNT_RX.search(line)
                cv = (int(m.group(1)) + int(m.group(2)) / 1e6) if m else None
                if seg is not None:
                    why = None
                    if now - last_arr > 5.0:
                        why = "توقف وصول الأسطر %.0f ث" % (now - last_arr)
                    elif cv is not None and seg["c_last"] is not None and cv < seg["c_last"] - 5:
                        why = "العداد رجع من %.1f إلى %.1f" % (seg["c_last"], cv)
                    if why:
                        seg_close(seg, why)
                        seg = None
                last_arr = now
                if seg is None:
                    seg = seg_open()
                seg["total"] += 1
                if cv is not None:
                    seg["c_last"] = cv
                    SL["c_last"] = cv
                    if seg["c_first"] is None:
                        seg["c_first"] = cv
                mt = re.match(r"^[A-Z][a-z]{2} +\d+ (\d\d:\d\d:\d\d)", line)
                if mt:
                    seg["t_last"] = mt.group(1)
                    SL["t_last"] = mt.group(1)
                cmpl_add(line)
                bbl_mk_extra(line)
                if MODEM_RX.search(line):
                    if line not in BBL_SEEN:   # الجهاز يعيد دفع السجل القديم عند كل اتصال: لا نعدّ السطر مرتين
                        BBL_SEEN.add(line)
                        bbl_add(line)
                if SL["kernel_only"] and " kernel" not in line and not line.startswith("\t"):
                    continue
                seg["fh"].write(line + "\n")
                seg["ring"].append(line)
                seg["lines"] += 1
                SL["lines"] = seg["lines"]
                if seg["lines"] % 200 == 0:
                    seg["fh"].flush()
        except Exception as e:
            SL["msg"] = "خطأ قراءة: %s: %s" % (type(e).__name__, e)
        try:
            p.terminate()
        except Exception:
            pass
        SL["proc"] = None
        if seg is not None:
            seg_close(seg, "انتهت العملية" if SL["on"] else "إيقاف يدوي")
        if SL["on"]:
            SL["msg"] = "انقطع الجهاز، بانتظار رجوعه… (آخر عداد %s)" % (("%.3f" % SL["c_last"]) if SL["c_last"] is not None else "—")
            time.sleep(0.3)
    SL["msg"] = SL["msg"] if SL["tool"] is False else "متوقف"

# ---------------------------------------------------------------- فلاش (استعادة النظام) عبر idevicerestore
FLASH = {"state": "idle", "msg": "", "pct": 0, "file": "", "dl": {"state": "idle", "pct": 0, "msg": "", "path": ""}, "proc": None}
IPSW_DIR = os.path.join(os.path.expanduser("~"), "NawafTeam_IPSW")

def flash_product():
    try:
        f = (get_device() or {}).get("fields", {})
    except Exception:
        return ""
    for k in ("Model Identifier", "Product Type"):
        v = str(f.get(k, ""))
        if v.startswith("iPhone"):
            return v
    return ""

def flash_list(product):
    import urllib.request
    req = urllib.request.Request("https://api.ipsw.me/v4/device/" + urllib.parse.quote(product) + "?type=ipsw", headers={"User-Agent": "NawafTeam/1.8"})
    with urllib.request.urlopen(req, timeout=25) as r:
        d = json.loads(r.read().decode("utf-8"))
    out = []
    for fw in d.get("firmwares", []):
        out.append({"version": fw.get("version", ""), "build": fw.get("buildid", ""), "url": fw.get("url", ""),
                    "size": fw.get("filesize", 0), "signed": bool(fw.get("signed")), "date": str(fw.get("releasedate", ""))[:10]})
    out.sort(key=lambda x: (not x["signed"], [-int(p) if p.isdigit() else 0 for p in re.split(r"[.]", x["version"])]))
    return out

def flash_download_job(url, dest):
    import urllib.request
    D = FLASH["dl"]
    D.update({"state": "running", "pct": 0, "msg": "يبدأ التنزيل", "path": dest})
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "NawafTeam/1.8"})
        with urllib.request.urlopen(req, timeout=60) as r, open(dest + ".part", "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                if D.get("cancel"):
                    raise RuntimeError("أُلغي التنزيل")
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                got += len(b)
                D["pct"] = int(got * 100 / total) if total else 0
                D["msg"] = "%d MB" % (got >> 20)
        os.replace(dest + ".part", dest)
        D.update({"state": "done", "pct": 100, "msg": "اكتمل التنزيل"})
    except Exception as e:
        D.update({"state": "error", "msg": "فشل التنزيل: " + str(e)[:120]})
        try:
            os.remove(dest + ".part")
        except Exception:
            pass
    D["cancel"] = False

def flash_job(ipsw, erase):
    exe = find_tool("idevicerestore")
    FLASH.update({"state": "running", "pct": 0, "msg": "تشغيل idevicerestore", "file": ipsw})
    MON.reset()
    cmd = [exe, "-d"] + (["-e"] if erase else []) + [ipsw]
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
        FLASH["proc"] = p
        buf = b""
        while True:
            ch = p.stdout.read(1)
            if not ch:
                break
            if ch in (b"\n", b"\r"):
                ln = buf.decode("utf-8", "replace").strip()
                buf = b""
                if ln:
                    MON.feed(ln)
                    m = re.search(r"(\d{1,3})\s*%", ln)
                    if m and int(m.group(1)) <= 100:
                        FLASH["pct"] = int(m.group(1))
                    FLASH["msg"] = ln[:140]
            else:
                buf += ch
        rc = p.wait()
        FLASH["state"] = "done" if rc == 0 else "error"
        FLASH["msg"] = "انتهى الفلاش (الكود 0)" if rc == 0 else "توقف الفلاش بالكود %s، راجع السجل أدناه" % rc
    except Exception as e:
        FLASH["state"] = "error"
        FLASH["msg"] = "تعذر التشغيل: " + str(e)[:120]
    FLASH["proc"] = None

def flash_pick():
    try:
        import tkinter
        from tkinter import filedialog
        r = tkinter.Tk()
        r.withdraw()
        r.attributes("-topmost", True)
        p = filedialog.askopenfilename(title="IPSW", filetypes=[("IPSW", "*.ipsw"), ("All", "*.*")])
        r.destroy()
        return p or ""
    except Exception:
        return ""

def flash_route(path, q, body):
    if path == "flash/status":
        return {"state": FLASH["state"], "msg": FLASH["msg"], "pct": FLASH["pct"], "file": FLASH["file"], "dl": {k: v for k, v in FLASH["dl"].items() if k != "cancel"},
                "tool": bool(find_tool("idevicerestore")), "dir": IPSW_DIR}
    if path == "flash/pick":
        return {"ok": True, "path": flash_pick()}
    if path == "flash/list":
        prod = str(body.get("product") or "") or flash_product()
        if not prod:
            return {"ok": False, "error": "ما قدرت أعرف موديل الهاتف. وصّله بالـ USB أو اكتب المعرّف (مثل iPhone17,2)."}
        try:
            return {"ok": True, "product": prod, "items": flash_list(prod)}
        except Exception as e:
            return {"ok": False, "error": "تعذر جلب القائمة من api.ipsw.me (يحتاج إنترنت): " + str(e)[:100]}
    if path == "flash/download":
        url = str(body.get("url", ""))
        if not url.startswith("https://") or FLASH["dl"]["state"] == "running":
            return {"ok": False, "error": "رابط غير صالح أو تنزيل شغّال حالياً."}
        dest = os.path.join(IPSW_DIR, os.path.basename(urllib.parse.urlparse(url).path) or "firmware.ipsw")
        threading.Thread(target=flash_download_job, args=(url, dest), daemon=True).start()
        return {"ok": True, "path": dest}
    if path == "flash/download/cancel":
        FLASH["dl"]["cancel"] = True
        return {"ok": True}
    if path == "flash/start":
        ipsw = str(body.get("ipsw", "")).strip().strip('"')
        if FLASH["state"] == "running":
            return {"ok": False, "error": "الفلاش شغّال حالياً."}
        if not body.get("confirm"):
            return {"ok": False, "error": "لازم تأكيد."}
        if not ipsw.lower().endswith(".ipsw") or not os.path.isfile(ipsw):
            return {"ok": False, "error": "ملف IPSW غير موجود."}
        if not find_tool("idevicerestore"):
            return {"ok": False, "error": "الأداة idevicerestore غير موجودة. ضع idevicerestore.exe بجانب أدوات libimobiledevice (نفس مكان ideviceinfo)."}
        threading.Thread(target=flash_job, args=(ipsw, bool(body.get("erase", True))), daemon=True).start()
        return {"ok": True}
    if path == "flash/stop":
        p = FLASH.get("proc")
        if p:
            try:
                p.terminate()
            except Exception:
                pass
        return {"ok": True}
    return None

UI_HTML = r'''<!doctype html>
<html lang="ar" dir="ltr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nawaf Team v1.8 – Professional iPhone Repair &amp; Diagnostic Tool</title>
<style>
:root{
  --bg:#06101f;--panel:#0c1f38;--panel2:#0f2848;--line:rgba(70,150,255,.30);--line2:rgba(70,170,255,.65);
  --txt:#e9f2ff;--mut:#8ea8cc;--acc:#2f9bff;--cy:#35d0ff;--ok:#2ee66b;--bad:#ff4d5e;--warn:#ffb020;
  --glow:0 0 0 1px rgba(60,160,255,.35),0 0 18px rgba(40,140,255,.18);
}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%}
body{background:radial-gradient(1300px 640px at 50% -12%,#12356a 0%,var(--bg) 62%) fixed,var(--bg);color:var(--txt);
  font:14px/1.5 "Segoe UI",Tahoma,"Noto Sans Arabic","Noto Sans",Arial,sans-serif;display:flex;flex-direction:column;min-height:100vh}
button,input,textarea{font:inherit;color:inherit}
.ic{width:1.25em;height:1.25em;flex:none}
/* ---------- top ---------- */
.top{display:flex;align-items:stretch;gap:14px;padding:10px 16px;border-bottom:1px solid var(--line);background:linear-gradient(#0a1b33,#08162b)}
.brand{display:flex;align-items:center;gap:12px;padding:4px 14px 4px 6px;border:1px solid var(--line);border-radius:14px;background:rgba(15,40,72,.5);min-width:250px}
.brand svg{width:54px;height:54px}
.brand b{display:block;font-size:26px;font-style:italic;font-weight:800;letter-spacing:.2px;line-height:1}
.brand small{color:var(--mut);font-size:12px}
nav{display:flex;gap:8px;flex:1;justify-content:flex-end}
nav button{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;min-width:96px;padding:7px 8px;border-radius:12px;
  border:1px solid transparent;background:transparent;cursor:pointer;color:var(--txt);transition:.15s}
nav button .ic{width:26px;height:26px;color:var(--cy);margin-bottom:2px}
nav button b{font-size:13px;font-weight:600}
nav button small{font-size:11.5px;color:var(--mut)}
nav button:hover{background:rgba(47,155,255,.10)}
nav button.on{border-color:var(--line2);background:linear-gradient(#123a6e,#0e2a52);box-shadow:var(--glow)}
/* ---------- layout ---------- */
main{flex:1;padding:14px 16px;min-height:0}
.view{display:none}.view.on{display:block}
.card{background:linear-gradient(180deg,rgba(16,40,72,.92),rgba(10,28,52,.92));border:1px solid var(--line);border-radius:14px;padding:14px;box-shadow:var(--glow)}
.card h3{direction:rtl;margin:0 0 10px;font-size:16px;display:flex;align-items:center;gap:8px;justify-content:space-between}
.card h3 .l{display:flex;align-items:center;gap:8px}
.mut{color:var(--mut)}
.row{display:flex;gap:12px}.col{display:flex;flex-direction:column;gap:12px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:8px 14px;border-radius:10px;border:1px solid var(--line2);
  background:linear-gradient(#143e75,#0f2f5a);cursor:pointer;font-weight:600;font-size:13px;transition:.15s}
.btn:hover{filter:brightness(1.25)}
.btn.ghost{background:rgba(15,40,72,.6);border-color:var(--line)}
.btn.red{border-color:#ff6677;background:linear-gradient(#5a1626,#3d0f1b)}
.btn:disabled{opacity:.5;cursor:default}
input[type=text],textarea{width:100%;background:#071426;border:1px solid var(--line);border-radius:10px;padding:8px 10px;outline:none}
input[type=text]:focus,textarea:focus{border-color:var(--line2)}
textarea{resize:vertical;min-height:110px;font-family:Consolas,monospace;font-size:12.5px}
.tag{display:inline-block;padding:1px 9px;border-radius:7px;font-size:12px;font-weight:600;background:#12407a;color:#bfe0ff;min-width:62px;text-align:center}
.tag.Restore{background:#14507a}.tag.Error{background:#d63a4c;color:#fff}.tag.c3{background:#173a68}
.bar{height:9px;border-radius:9px;background:#0a1a30;border:1px solid var(--line);overflow:hidden}
.bar i{display:block;height:100%;background:linear-gradient(90deg,#1a7dff,#35d0ff);border-radius:9px;transition:width .4s}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--ok);box-shadow:0 0 8px var(--ok);margin-inline-end:6px}
.dot.off{background:#5b6b85;box-shadow:none}.dot.bad{background:var(--bad);box-shadow:0 0 8px var(--bad)}
.badge{padding:2px 9px;border-radius:8px;font-size:12px;border:1px solid var(--warn);color:var(--warn)}
/* ---------- device ---------- */
.dev{display:grid;grid-template-columns:230px 1fr;gap:14px}
.side{display:flex;flex-direction:column;gap:7px;padding:10px}
.side button{direction:rtl;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 12px;border-radius:11px;border:1px solid transparent;
  background:transparent;cursor:pointer;text-align:right;font-weight:600}
.side button .ic{color:var(--cy)}
.side button:hover{background:rgba(47,155,255,.10)}
.side button.on{border-color:var(--line2);background:linear-gradient(#123a6e,#0e2a52);box-shadow:var(--glow)}
.side .soon::after{content:"قريباً";font-size:10.5px;color:var(--warn);border:1px solid var(--warn);border-radius:6px;padding:0 5px;margin-inline-start:4px}
.devtop{display:grid;grid-template-columns:1fr 250px;gap:14px}
.devhead{display:flex;gap:18px}
.phone{width:150px;flex:none;display:flex;align-items:center;justify-content:center}
.kvs{display:grid;grid-template-columns:1.25fr 1fr;gap:4px 26px;margin-top:8px;font-size:13px}
.kvs div{display:flex;justify-content:space-between;gap:10px;border-bottom:1px dashed rgba(80,150,255,.15);padding:2px 0}
.kvs span:first-child{color:var(--mut)}
.kvs .v{font-weight:600;direction:ltr;text-align:left}
.ok{color:var(--ok)}.bad{color:var(--bad)}
.modes{display:flex;gap:10px;justify-content:center;margin-top:12px}
.modes .btn{min-width:150px;opacity:.75;cursor:default}
.modes .btn.on{opacity:1;border-color:var(--cy);box-shadow:var(--glow);background:linear-gradient(#17508f,#0f3a70)}
.bat{display:flex;align-items:center;gap:8px;margin:2px 0 8px}
.bat .cell{width:52px;height:22px;border:2px solid #cfe3ff;border-radius:5px;padding:2px;position:relative}
.bat .cell i{display:block;height:100%;background:var(--ok);border-radius:2px}
.bat .cell::after{content:"";position:absolute;right:-6px;top:5px;width:4px;height:8px;background:#cfe3ff;border-radius:0 2px 2px 0}
.dev2{display:grid;grid-template-columns:1.15fr 1fr;gap:14px;margin-top:14px}
.log{height:176px;overflow:auto;background:#06142a;border:1px solid var(--line);border-radius:10px;padding:6px;font-family:Consolas,"Segoe UI",monospace;font-size:13px}
.log .ln{display:flex;gap:9px;align-items:center;padding:4px 6px;border-radius:6px;direction:ltr}
.log .ln.err{background:rgba(255,77,94,.18);border:1px solid rgba(255,77,94,.55);color:#ffd2d7}
.log .ln .t{color:var(--mut);white-space:nowrap}
.acts{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
.acts .btn{direction:rtl;flex:1;min-width:84px;flex-direction:column;padding:7px 6px;font-size:12px}
.errbox{border:1px solid #ff5566;background:linear-gradient(#3a0f1b,#2a0a14);border-radius:12px;padding:10px 14px;text-align:center;font-size:20px;box-shadow:0 0 18px rgba(255,60,80,.25)}
.errbox b{font-size:32px;letter-spacing:2px;color:#ff6a78}
.an h4{margin:12px 0 4px;font-size:13px;color:var(--cy)}
.an ol,.an ul{margin:4px 0 0;padding-inline-start:20px}
.an li{margin:2px 0}
.fix{margin-top:12px;border:1px solid rgba(46,230,107,.5);background:linear-gradient(rgba(14,60,38,.55),rgba(8,36,24,.55));border-radius:12px;padding:10px 12px}
.fix label{display:flex;gap:9px;align-items:flex-start;margin:5px 0;cursor:pointer}
.fix input{accent-color:#2f9bff;margin-top:4px}
.empty{padding:26px 10px;text-align:center;color:var(--mut)}
.empty,.note,label.mut{direction:rtl}
label.mut{display:block;margin-bottom:4px}
.dev2{align-items:start}
/* ---------- generic ---------- */
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.list .it{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:8px 10px;border-bottom:1px solid rgba(80,150,255,.15)}
.list .it:hover{background:rgba(47,155,255,.07)}
.bars .b{margin:8px 0}.bars .b span{display:flex;justify-content:space-between;margin-bottom:3px}
.note{font-size:12.5px;color:var(--mut);margin-top:10px}
.pill{display:inline-block;border:1px solid var(--line);border-radius:8px;padding:1px 8px;margin:2px;font-size:12px;background:#0a2140;cursor:pointer}
.stat{font-size:26px;font-weight:700}
table{width:100%;border-collapse:collapse;font-size:12.5px}td,th{padding:5px 8px;border-bottom:1px solid rgba(80,150,255,.15);text-align:left}
.status{display:flex;gap:22px;align-items:center;justify-content:space-between;padding:7px 16px;border-top:1px solid var(--line);background:#07142a;font-size:12.5px;color:var(--mut)}
.toast{position:fixed;bottom:48px;left:50%;transform:translateX(-50%);background:#12407a;border:1px solid var(--line2);padding:9px 18px;border-radius:10px;display:none;z-index:9}
@media (max-width:1100px){.dev,.devtop,.dev2,.grid2,.grid3{grid-template-columns:1fr}nav button{min-width:70px}.brand{min-width:0}}
#d-mon{display:none !important}
#side{display:none !important}
.dev{grid-template-columns:1fr !important}
#m-file,#m-file+*,label.btn:has(#m-file),#m-winprobe,#m-demo,#m-pasteClr{display:none !important}
#v-monitor .acts{display:none !important}
#v-monitor .dev2{grid-template-columns:1fr}
#v-monitor .mon-log{height:62vh;min-height:380px}
#v-baseband > .card:not(#bl-card){display:none !important}
.gear{font-size:46px;display:inline-block;animation:gspin 2s linear infinite}@keyframes gspin{to{transform:rotate(360deg)}}.tlt{width:auto;font-size:12.5px;border-collapse:collapse;margin:4px 0}.tlt th,.tlt td{padding:2px 16px 2px 0;text-align:left;white-space:nowrap}.tlt td:first-child,.tlt th:first-child{min-width:230px}
html.simple #bl-state,html.simple #bl-msg,html.simple .bl-desc,html.simple #bb-card,html.simple #fl-card,html.simple #bb-notes-card,html.simple #bb-res{display:none!important}

/* ===== v1.5 design C: sidebar + flat cards ===== */
:root{--bg:#070d18;--panel:#0f1a2b;--panel2:#13233a;--line:rgba(60,130,230,.22);--line2:rgba(53,208,255,.55);--glow:0 0 0 1px rgba(53,208,255,.06),0 8px 24px rgba(0,0,0,.28)}
body{background:radial-gradient(900px 480px at 85% -10%,#10305e 0%,var(--bg) 60%) fixed,var(--bg);font-family:"IBM Plex Sans Arabic","Segoe UI",Tahoma,"Noto Sans Arabic",Arial,sans-serif}
header.top{position:fixed;top:0;right:0;bottom:0;width:96px;z-index:50;flex-direction:column;align-items:center;gap:10px;padding:16px 8px;border-bottom:0;border-left:1px solid var(--line);background:linear-gradient(180deg,#0d1828,#09121f);overflow-y:auto}
.brand{min-width:0;width:100%;padding:0;border:0;background:none;justify-content:center}
.brand svg{width:50px;height:50px}
.brand div{display:none}
nav{flex-direction:column;flex:none;gap:6px;width:100%;justify-content:flex-start}
nav button{min-width:0;width:100%;padding:10px 4px;border-radius:14px}
nav button .ic{width:24px;height:24px}
nav button b{font-size:12px}
nav button small{display:none}
nav button.on{background:rgba(53,208,255,.12);border-color:var(--line2);box-shadow:inset 0 0 0 1px rgba(53,208,255,.15)}
nav button.on .ic{color:#fff}
main{margin-right:96px;padding:22px 26px}
footer.status{margin-right:96px;background:#08111e}
.card{background:linear-gradient(160deg,#122238 0%,#0f1a2b 70%);border:1px solid var(--line);border-radius:20px;padding:18px 20px;box-shadow:var(--glow)}
.btn{border-radius:14px;padding:10px 20px;background:linear-gradient(135deg,#35d0ff,#2f6bff);border:0;color:#04101f;font-weight:700}
.btn.ghost{background:rgba(53,208,255,.08);border:1px solid var(--line);color:var(--txt)}
.btn.red{background:linear-gradient(#7a1d30,#561322);color:#fff;border:1px solid #ff6677}
input[type=text],textarea{border-radius:14px;background:#0a1424}
.bar{height:12px;border-radius:12px;background:#0b1626}
.bar i{background:linear-gradient(90deg,#2f6bff,#35d0ff);border-radius:12px}
.devtop>.card:first-child{position:relative;overflow:hidden;background:linear-gradient(160deg,#143a5c 0%,#0f1a2b 72%);border-color:rgba(53,208,255,.28)}
.devtop>.card:first-child::after{content:"";position:absolute;left:-30px;bottom:-40px;width:230px;height:230px;opacity:.07;background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 128' fill='none'%3E%3Crect x='30' y='8' width='68' height='112' rx='16' stroke='%2335d0ff' stroke-width='6'/%3E%3Cpath d='M50 94V48L78 94V48' stroke='%232f6bff' stroke-width='9' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") no-repeat center/contain;pointer-events:none}
.devtop>.card:first-child>*{position:relative;z-index:1}
.bat .cell{border-radius:8px;border-color:#9fc4ee}
.card h3{font-size:17px}
@media (max-width:1100px){header.top{width:72px}main,footer.status{margin-right:72px}.brand svg{width:38px;height:38px}}

.scn{display:flex;align-items:center;gap:22px;border:1px solid var(--line2);border-radius:20px;padding:18px 22px;background:linear-gradient(160deg,#133a5e,#0f1a2b 75%);box-shadow:0 0 28px rgba(53,208,255,.12)}
.rgw{position:relative;width:132px;height:132px;flex:none}
.rg{width:132px;height:132px;transform:rotate(-90deg)}
.rgb,.rgf{fill:none;stroke-width:9}.rgb{stroke:#1a2c44}
.rgf{stroke:url(#rgg);stroke:#35d0ff;stroke-linecap:round;transition:stroke-dasharray .9s linear;filter:drop-shadow(0 0 6px rgba(53,208,255,.6))}
.rgt{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}
.rgt b{font-size:30px;font-weight:800;line-height:1}.rgt small{color:var(--mut);margin-top:3px}
.sweep{position:absolute;inset:14px;border-radius:50%;background:conic-gradient(from 0deg,rgba(53,208,255,0) 0,rgba(53,208,255,.22) 80deg,rgba(53,208,255,0) 81deg);animation:gspin 2.4s linear infinite;pointer-events:none}
.scr{flex:1;min-width:0}
.scs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.scc{padding:5px 13px;border-radius:999px;border:1px dashed var(--line);color:var(--mut);font-size:12.5px;font-weight:600}
.scc.d{border:1px solid rgba(46,230,107,.5);color:var(--ok);background:rgba(46,230,107,.08)}
.scc.a{border:1px solid var(--cy);color:var(--cy);background:rgba(53,208,255,.12);animation:scp 1.4s ease-in-out infinite}
@keyframes scp{50%{box-shadow:0 0 14px rgba(53,208,255,.5)}}
.scbar{height:14px}

.rsh{display:flex;align-items:center;gap:14px;padding:14px 18px;border-radius:20px;background:linear-gradient(160deg,#143a5c,#0f1a2b 75%);border:1px solid rgba(53,208,255,.3);margin-bottom:12px}
.rsh b{font-size:20px;display:block}.rsh small{color:var(--mut)}
.rsb{counter-reset:stp}
.rsb>div[style*="border-inline-start:3px solid #2ecc71"]{border:1px solid rgba(46,230,107,.35)!important;border-radius:20px!important;background:linear-gradient(160deg,rgba(46,230,107,.07),#0f1a2b 80%)!important;padding:16px 18px!important}
.rsb .fstep{counter-increment:stp;position:relative;border:1px solid var(--line)!important;border-radius:16px!important;background:#0c1626!important;padding:12px 56px 12px 14px!important;margin-top:10px!important}
.rsb .fstep::before{content:counter(stp);position:absolute;right:14px;top:12px;width:30px;height:30px;border-radius:50%;background:linear-gradient(135deg,#35d0ff,#2f6bff);color:#04101f;font-weight:800;display:grid;place-items:center}
.rsb .fend{border:1px solid rgba(46,230,107,.4)!important;border-radius:14px!important}
.nodev .phone svg{animation:flt 3s ease-in-out infinite}
@keyframes flt{50%{transform:translateY(-8px)}}
.nodh{grid-column:1/-1;width:100%;margin-top:14px;padding:14px 18px;border:1px dashed var(--line2);border-radius:16px;color:var(--cy);font-size:15px;line-height:1.8;background:rgba(53,208,255,.06);animation:scp 2s ease-in-out infinite}

/* ===== v1.5 page hero + monitor/panic polish ===== */
.pgh{display:flex;align-items:center;gap:14px;margin:0 0 16px;padding:14px 20px;border-radius:20px;border:1px solid rgba(53,208,255,.28);background:linear-gradient(160deg,#143a5c,#0f1a2b 78%)}
.pgh b{display:block;font-size:22px;line-height:1.2}.pgh small{color:var(--mut);letter-spacing:.5px}
.btn,.card h3,.note,.pgh{unicode-bidi:plaintext}
input::placeholder,textarea::placeholder{unicode-bidi:plaintext;opacity:.6}
#v-monitor.on{display:flex;flex-direction:column;gap:14px}
#v-monitor>.card{margin:0!important}
#v-monitor>.card:nth-of-type(1){order:1}#v-monitor>#m-mon{order:2}#v-monitor>.card:nth-of-type(2){order:3}
#v-monitor .card:first-child h3 span.l::before{content:"";display:inline-block;width:4px;height:16px;border-radius:2px;background:var(--cy);margin-inline-end:8px}
#au-now{width:100%}
#au-state{padding:12px 16px;border:1px dashed var(--line2);border-radius:14px;color:var(--cy);margin-bottom:10px;background:rgba(53,208,255,.06)}
#au-state:empty{display:none}
.card h3 .l::before{content:"";width:4px;height:16px;border-radius:2px;background:var(--cy);display:inline-block}

.rwrap{display:flex;align-items:center;gap:16px}
.rw{position:relative;width:96px;height:96px;flex:none}.rw svg{width:96px;height:96px;transform:rotate(-90deg)}
.rb,.rf{fill:none;stroke-width:10}.rb{stroke:#1a2c44}.rf{stroke:var(--cy);stroke-linecap:round;transition:stroke-dashoffset .6s;filter:drop-shadow(0 0 5px rgba(53,208,255,.5))}
#b-ring{stroke:var(--ok)}
.rc{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center}.rc b{font-size:19px;line-height:1}.rc small{font-size:10.5px;color:var(--mut);margin-top:2px}
.brand svg{width:56px;height:56px}
/* license overlay */
body>div[style*="99999"]{background:radial-gradient(900px 500px at 50% 0%,#12356a,#050d1a 70%)!important}
body>div[style*="99999"]>div{background:linear-gradient(160deg,#122238,#0f1a2b);border:1px solid rgba(53,208,255,.28);border-radius:26px;padding:34px 30px!important;box-shadow:0 18px 50px rgba(0,0,0,.45),0 0 40px rgba(53,208,255,.08)}
body>div[style*="99999"]>div::before{content:"";display:block;width:92px;height:92px;margin:0 auto 12px;background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 128' fill='none'%3E%3Crect x='30' y='8' width='68' height='112' rx='16' stroke='%2335d0ff' stroke-width='6'/%3E%3Cpath d='M50 94V48L78 94V48' stroke='%232f6bff' stroke-width='9' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E") no-repeat center/contain;filter:drop-shadow(0 0 12px rgba(53,208,255,.5))}
body>div[style*="99999"] h2{margin:0 0 8px;font-size:24px}
body>div[style*="99999"] input{border-radius:14px!important;background:#0a1424!important;border:1px solid rgba(60,130,230,.35)!important;color:#e9f2ff!important;padding:12px 14px!important;font-size:15px}
body>div[style*="99999"] button{border-radius:14px!important;background:linear-gradient(135deg,#35d0ff,#2f6bff)!important;color:#04101f!important;font-weight:700;border:0!important;padding:11px 28px!important;cursor:pointer}
</style>
</head>
<body>
<header class="top">
  <div class="brand">
    <svg viewBox="0 0 128 128" fill="none" aria-hidden="true"><rect x="30" y="8" width="68" height="112" rx="16" stroke="#35d0ff" stroke-width="6"/><path d="M50 94V48L78 94V48" stroke="#2f6bff" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/></svg>
    <div><b>Nawaf Team</b><small>iPhone Repair &amp; Diagnostic</small></div>
  </div>
  <nav id="nav"></nav>
</header>

<main>
<!-- ================= HOME ================= -->
<section class="view" id="v-home">
  <div class="grid3">
    <div class="card"><h3><span class="l">USB — الجهاز</span></h3><div id="h-dev" class="mut">…</div></div>
    <div class="card"><h3><span class="l">آخر خطأ مكتشف</span></h3><div id="h-err" class="mut">لا يوجد خطأ حتى الآن.</div></div>
    <div class="card"><h3><span class="l">قاعدة الأعطال</span></h3><div class="stat" id="h-db">—</div><div class="mut" dir="rtl">قاعدة محلية: أكواد استعادة وأنماط Panic</div></div>
  </div>
  <div class="card" style="margin-top:14px">
    <h3><span class="l">ابدأ من هنا</span></h3>
    <div class="grid3" id="h-quick"></div>
    <p class="note" dir="auto">التحليل استدلالي من السجلات والأكواد المعروفة، ولا يغني عن القياس الفعلي على البورد (تيار الإقلاع، خطوط التغذية، المقاومة). كل ما أضفت سجلات حقيقية للقاعدة تتحسن الدقة.</p>
  </div>
</section>

<!-- ================= DEVICE ================= -->
<section class="view" id="v-device">
  <div class="dev">
    <aside class="card side" id="side"></aside>
    <div>
      <div class="devtop">
        <div class="card">
          <div class="devhead">
            <div class="phone">
              <svg viewBox="0 0 120 220" width="130" aria-hidden="true"><defs><linearGradient id="pg" x1="0" x2="1"><stop offset="0" stop-color="#9aa6b8"/><stop offset=".5" stop-color="#e3e9f2"/><stop offset="1" stop-color="#8793a6"/></linearGradient><linearGradient id="sg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#16356a"/><stop offset="1" stop-color="#050b18"/></linearGradient></defs>
              <rect x="14" y="8" width="94" height="204" rx="20" fill="url(#pg)"/><rect x="19" y="13" width="84" height="194" rx="16" fill="url(#sg)"/>
              <rect x="46" y="20" width="30" height="8" rx="4" fill="#000"/>
              <rect x="2" y="26" width="46" height="54" rx="14" fill="url(#pg)" opacity=".0"/>
              <circle cx="61" cy="120" r="34" fill="none" stroke="#2f9bff" stroke-opacity=".35" stroke-width="2"/><circle cx="61" cy="120" r="20" fill="none" stroke="#35d0ff" stroke-opacity=".5" stroke-width="2"/></svg>
            </div>
            <div style="flex:1">
              <div style="display:flex;align-items:center;gap:10px"><h2 id="d-name" style="margin:0;font-size:26px">—</h2><span class="badge" id="d-demo" style="display:none">DEMO</span></div>
              <div id="d-status" style="margin:3px 0 4px"></div>
              <div class="kvs" id="d-kvs"></div>
            </div>
          </div>
        </div>
        <div class="col">
          <div class="card"><div class="rwrap"><div class="rw"><svg viewBox="0 0 120 120"><circle class="rb" cx="60" cy="60" r="50"/><circle class="rf" id="b-ring" cx="60" cy="60" r="50"/></svg><div class="rc"><b class="ok" id="b-pct">—</b><small>Battery</small></div></div><div class="kvs" style="grid-template-columns:1fr;flex:1"><div><span>Cycles :</span><span class="v" id="b-cyc">—</span></div><div><span>Health :</span><span class="v" id="b-health">—</span></div></div><i id="b-fill" style="width:0" hidden></i></div></div>
          <div class="card"><div class="rwrap"><div class="rw"><svg viewBox="0 0 120 120"><circle class="rb" cx="60" cy="60" r="50"/><circle class="rf" id="s-ring" cx="60" cy="60" r="50"/></svg><div class="rc"><b id="s-pc">—</b><small>Storage</small></div></div><div class="mut" id="s-txt" dir="ltr" style="flex:1;text-align:right">—</div><i id="s-fill" style="width:0" hidden></i></div></div>
          <div class="card" id="m-card" style="display:none"><b>Modem / Baseband</b><div id="m-body" style="margin-top:8px"></div>
            <button class="btn" id="m-scanbtn" hidden style="display:none">Scan Device Log (up to 1 min)</button><div id="m-scan" hidden style="display:none"></div></div>
        </div>
      </div>
      <div class="modes" id="d-modes"></div>
      <div class="dev2" id="d-mon"></div>
    </div>
  </div>
</section>

<!-- ================= PANIC ================= -->
<section class="view" id="v-panic">
  <div class="grid2">
    <div class="card">
      <h3><span class="l">سحب الـ Panic</span></h3>
      <div class="acts" style="margin:0 0 10px"><button class="btn" id="au-now" style="flex-direction:row">Pull Panic</button></div>
      <div id="au-state" class="mut" dir="auto"></div>
      
    </div>
    <div class="card">
      <h3><span class="l">تحليل الـ Panic</span></h3>
      <div id="au-out" class="mut" dir="auto">لا توجد نتيجة بعد.</div>
    </div>
  </div>
</section>

<!-- ================= MONITOR ================= -->
<section class="view" id="v-monitor">
  <div class="card" style="margin-bottom:14px">
    <h3><span class="l">Monitor Source</span><span class="mut" id="m-src"></span></h3>
    <div class="row" style="align-items:center;flex-wrap:wrap">
      <input type="text" id="m-folder" dir="auto" placeholder="3uTools log folder" style="flex:1;min-width:260px">
      <button class="btn" id="m-start">Start Monitor</button>
      <button class="btn ghost" id="m-stop">Stop</button>
      <button class="btn ghost" id="m-find">Locate Log</button>
      <label class="btn ghost">Analyze Log File…<input type="file" id="m-file" hidden></label>
      <button class="btn" id="m-win">Auto-Read 3uTools</button>
      <button class="btn ghost" id="m-winprobe">Test Read</button>
      <button class="btn ghost" id="m-demo">Demo</button>
    </div>
    <textarea id="m-paste" dir="auto" placeholder="… or paste 3uTools log text here, then press Analyze Text" style="margin-top:10px"></textarea>
    <div class="note" dir="ltr">Easier: inside the 3uTools log press Ctrl+A then Ctrl+C, and the program captures and analyzes the log automatically.</div>
    <div class="row" style="margin-top:8px"><button class="btn" id="m-pasteGo">Analyze Text</button><button class="btn ghost" id="m-pasteClr">Clear</button></div>
  </div>
  <div class="card" style="margin-bottom:14px">
    <h3><span class="l">System Flash (iPhone Restore)</span><span class="mut" id="fl-tool"></span></h3>
    <div class="note" dir="ltr">Warning: Restore erases all phone data. It does not remove iCloud lock.</div>
    <div class="row" style="align-items:center;flex-wrap:wrap;margin-top:8px">
      <input type="text" id="fl-file" dir="auto" placeholder="IPSW file path" style="flex:1;min-width:260px">
      <button class="btn ghost" id="fl-pick">Choose IPSW…</button>
      <button class="btn ghost" id="fl-list">Available Versions</button>
    </div>
    <div id="fl-items" style="margin-top:8px"></div>
    <div class="row" style="align-items:center;flex-wrap:wrap;margin-top:8px">
      <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="fl-erase" checked> Full Erase</label>
      <button class="btn" id="fl-go">Start Flash</button>
      <button class="btn ghost" id="fl-stop">Stop</button>
    </div>
    <div class="mut" id="fl-msg" dir="auto" style="margin-top:8px"></div>
  </div>
  <div class="dev2" id="m-mon" style="margin-top:0"></div>
</section>

<!-- ================= FAULTS ================= -->
<section class="view" id="v-baseband">
  <div class="card" id="bl-card" style="margin-bottom:14px">
    <h3><span class="l">Baseband Scan</span><span class="mut" id="bl-state"></span></h3>
    <div class="mut bl-desc" dir="ltr" style="margin-bottom:8px;text-align:left">Reads the live device log (idevicesyslog) for 100 seconds with a countdown (tip: press Scan, then Restart Phone to capture the boot log), collects every modem-related line with its count and timestamps, then shows the evidence automatically. No verdict is made by the program; lines are only explained by your own documented rules file.</div>
    <div class="row" style="gap:8px;flex-wrap:wrap;align-items:center" dir="rtl">
      <button class="btn" id="bl-scan" style="flex-direction:row">Baseband Scan (100 s)</button>
      <button class="btn ghost" id="bl-stop" style="flex-direction:row">Stop</button>
      <button class="btn ghost" id="bl-rst" style="flex-direction:row">Restart Phone</button>
      <span id="bl-cnt" style="display:none" dir="ltr"></span>
    </div>
    <div id="bl-prog" style="display:none;margin-top:10px"></div>
    <div id="bl-msg" class="note" dir="auto"></div>
    <div id="bl-out" dir="rtl" style="margin-top:8px"></div>
  </div>
  <div class="card" id="bb-card" style="margin-bottom:14px">
    <h3><span class="l">تحليل الباسباند / المودم</span><span class="mut" id="bb-dev"></span></h3>
    <div class="mut" dir="rtl" style="margin-bottom:6px">اختر موديل الجهاز، وصّل الهاتف وهو شغّال (Normal) ثم ابدأ التحليل.</div>
    <div class="row" style="align-items:center;flex-wrap:wrap">
      <select id="bb-model" style="flex:1;min-width:220px;background:#071426;border:1px solid var(--line);border-radius:10px;padding:9px 10px;color:inherit" dir="ltr"></select>
      <button class="btn" id="bb-go">ابدأ التحليل</button>
    </div>
    <label class="row" style="margin-top:8px;gap:8px;cursor:pointer" dir="rtl"><input type="checkbox" id="bb-restart"><span>أعد تشغيل الهاتف قبل الفحص (يلتقط سجل الإقلاع، وقد يطلب رمز القفل بعد الإقلاع)</span></label>
    <div class="row" style="margin-top:8px;gap:8px;align-items:center" dir="rtl"><span>مدة التقاط السجل:</span><select id="bb-secs"><option value="15">15 ثانية</option><option value="30" selected>30 ثانية</option><option value="60">60 ثانية</option></select>
      <label class="btn ghost" style="cursor:pointer;margin-inline-start:auto">حلّل ملف سجل (بدون الهاتف)<input type="file" id="bb-logfile" accept=".txt,.log,.syslog,text/plain" style="display:none"></label></div>
    <div id="bb-fres" style="display:none;margin-top:10px" dir="rtl"></div>
    <div class="row" style="margin-top:8px"><button class="btn ghost" id="bb-basesave">احفظ الجهاز الموصول كـ«جهاز سليم» لهذا الموديل</button></div>
    <div id="bb-prog" style="display:none;margin-top:12px"></div>
  </div>
  <div class="card" id="bb-res" style="display:none"></div>
  <div class="card" id="fl-card" style="margin-top:14px;display:none">
    <h3><span class="l">قياس موجّه (خطوة بخطوة)</span><span class="mut" id="fl-topic"></span></h3>
    <div class="mut" id="fl-src" dir="rtl" style="font-size:12px;margin-bottom:8px"></div>
    <div id="fl-body" dir="rtl"></div>
  </div>
  <div class="card" id="bb-notes-card" style="margin-top:14px;display:none">
    <h3><span class="l">ملاحظاتي لهذا الموديل</span></h3>
    <textarea id="bb-notes" dir="rtl" placeholder="اكتب قائمة فحصك لهذا الموديل، كل بند بسطر (مثلاً: قياس خط كذا، فحص لحام كذا). تظهر بعد كل تحليل لنفس الموديل كقائمة تؤشر عليها."></textarea>
    <div class="row" style="margin-top:8px"><button class="btn" id="bb-notesave">حفظ الملاحظات</button></div>
  </div>
</section>

<section class="view" id="v-faults">
  <div class="card" style="margin-bottom:14px"><input type="text" id="f-q" placeholder="ابحث في الحالات المحققة…" dir="auto"></div>
  <div class="grid2">
    <div id="f-codes" style="display:none"></div><div id="f-panic" style="display:none"></div>
  </div>
  <div class="card" style="margin-top:14px"><h3><span class="l">مكتبة الأعطال</span></h3><div class="list" id="f-hw"><div class="empty">المكتبة فارغة — لا توجد أعطال مسجلة.</div></div></div>
  <div id="f-sens" style="display:none"></div>
</section>

<!-- ================= SCHEMATICS ================= -->
<section class="view" id="v-schem">
  <div class="card">
    <h3><span class="l">المخططات (ZXW / JCID / PDF)</span><span class="mut" id="sc-dir"></span></h3>
    <div class="row"><input type="text" id="sc-q" placeholder="ابحث باسم الموديل أو الملف…" dir="auto"><button class="btn ghost" id="sc-ref">Refresh</button></div>
    <div class="list" id="sc-list" style="margin-top:10px;max-height:60vh;overflow:auto"></div>
    <p class="note" dir="auto">البرنامج لا يحمّل مخططات، بل يفهرس الملفات الموجودة عندك في مجلد تحدده من الإعدادات ويفتحها ببرنامجها الافتراضي.</p>
  </div>
</section>

<!-- ================= DIAGNOSTICS ================= -->
<section class="view" id="v-diag">
  <div style="max-width:980px">
    <div class="card">
      <h3><span class="l">Diagnostics / IORegistry</span></h3>
      <div class="note" style="margin-top:0">قراءة فقط من الهاتف عبر الأداة <b dir="ltr">idevicediagnostics</b>. الناتج يظهر خاماً كما رجع من الأداة، وكل تشغيل يُحفظ في <span dir="ltr">~/NawafTeam_logs</span>.</div>
      <div class="row" style="margin-top:12px;gap:8px;align-items:center;flex-wrap:wrap" dir="rtl">
        <select id="dg-cmd" dir="ltr" style="background:#071426;border:1px solid var(--line);border-radius:10px;padding:9px 10px;color:inherit">
          <option value="ioreg">ioreg — IORegistry كامل</option>
          <option value="ioregentry">ioregentry — عنصر محدد</option>
          <option value="diagnostics">diagnostics — معلومات تشخيص</option>
          <option value="mobilegestalt">mobilegestalt — مفاتيح</option>
        </select>
        <select id="dg-plane" dir="ltr" style="background:#071426;border:1px solid var(--line);border-radius:10px;padding:9px 10px;color:inherit">
          <option value="">Plane: الافتراضي</option><option>IODeviceTree</option><option>IOPower</option><option>IOService</option>
        </select>
        <input type="text" id="dg-arg" dir="ltr" style="flex:1;min-width:200px;display:none">
        <button class="btn" id="dg-run" style="flex-direction:row">تنفيذ</button>
      </div>
      <div class="row" style="margin-top:14px;gap:8px;align-items:center;flex-wrap:wrap" dir="rtl">
        <b>سكان بيسباند</b>
        <input type="text" id="dg-kw" dir="ltr" value="baseband|modem|bbpmu|pcie|commcenter" style="flex:1;min-width:240px">
        <button class="btn" id="dg-bb" style="flex-direction:row">ابدأ</button>
      </div>
      <div class="note">يقرأ IORegistry كاملاً ويعرض العناصر اللي اسمها أو كلاسها يطابق الكلمات (مفصولة بـ |)، مع خصائصها كما هي. لا يعطي حكماً ولا تفسيراً.</div>
      <div id="dg-msg" class="note" dir="auto"></div>
    </div>
    <div class="card" id="dg-bbcard" style="margin-top:14px;display:none"><h3><span class="l">نتيجة سكان البيسباند</span></h3><div id="dg-bbres" dir="ltr"></div></div>
    <div class="card" style="margin-top:14px">
      <h3><span class="l">الناتج الخام</span></h3>
      <div class="row" style="gap:8px;align-items:center;flex-wrap:wrap" dir="rtl">
        <input type="text" id="dg-filter" dir="ltr" placeholder="فلتر: يعرض فقط الأسطر اللي فيها هذا النص" style="flex:1;min-width:220px">
        <button class="btn ghost" id="dg-copy" style="flex-direction:row">نسخ</button>
        <span class="mut" id="dg-cnt"></span>
      </div>
      <textarea id="dg-out" dir="ltr" readonly style="margin-top:10px;min-height:340px" placeholder="اضغط «تنفيذ»"></textarea>
    </div>
  </div>
</section>

<!-- ================= LIVE LOG ================= -->
<section class="view" id="v-live">
  <div style="max-width:980px">
    <div class="card">
      <h3><span class="l">تحليل اللوغ المباشر (3uTools Real-time Logs)</span></h3>
      <div class="note" style="margin-top:0">اختر ملف أو عدة ملفات <b dir="ltr">…_iPhone.log</b>. التحليل يتم محلياً بالمتصفح، ولا يُرسل أي شيء. يعرض الأرقام كما هي من اللوغ بدون تفسير.</div>
      <div class="row" style="margin-top:12px;gap:8px;align-items:center;flex-wrap:wrap" dir="rtl">
        <input type="file" id="lv-files" multiple accept=".log,.txt" style="flex:1;min-width:220px">
        <button class="btn" id="lv-run" style="flex-direction:row">تحليل</button>
      </div>
      <div class="row" style="margin-top:10px;gap:8px;align-items:center;flex-wrap:wrap" dir="rtl">
        <b>كلمات للعدّ</b>
        <input type="text" id="lv-kw" dir="ltr" value="SPMI is not ready|ISP_PowerOnCamera|paniclog|Pearl|panic" style="flex:1;min-width:240px">
      </div>
      <div id="lv-msg" class="note" dir="auto"></div>
    </div>
    <div id="lv-res" style="margin-top:14px"></div>
    <div class="card" style="margin-top:14px">
      <h3><span class="l">لوغ مباشر من البرنامج (بدل 3uTools)</span><span class="mut" id="sl-state"></span></h3>
      <div class="note" style="margin-top:0">بيقرأ لوغ الجهاز عبر <b dir="ltr">idevicesyslog</b>، وبيكتبه لملف بدون حد حجم. أول ما الجهاز ينقطع (ريستارت) بيحفظ <b>آخر 300 سطر</b> مع آخر عداد، وبيرجع يتصل لحاله لما الجهاز يقلع. سكّر نافذة Real-time Logs بـ 3uTools قبل ما تبدأ (ما تأكدت إذا بتتعارض).</div>
      <div class="row" style="margin-top:12px;gap:8px;align-items:center;flex-wrap:wrap" dir="rtl">
        <button class="btn" id="sl-start" style="flex-direction:row">ابدأ التسجيل</button>
        <button class="btn ghost" id="sl-stop" style="flex-direction:row">إيقاف</button>
        <label class="mut" style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="sl-kernel" checked> kernel فقط (أخف)</label>
      </div>
      <div class="row" style="margin-top:8px;gap:8px;flex-wrap:wrap" dir="rtl">
        <button class="btn ghost" id="sl-dl-tail" style="flex-direction:row">تنزيل آخر الأسطر قبل كل انقطاع (ZIP)</button>
        <button class="btn ghost" id="sl-dl-full" style="flex-direction:row">تنزيل التسجيل الكامل (ZIP)</button>
      </div>
      <div id="sl-msg" class="note" dir="auto"></div>
      <div id="sl-sess" dir="rtl" style="margin-top:8px"></div>
    </div>
  </div>
</section>

<!-- ================= TOOLS ================= -->
<section class="view" id="v-tools">
  <div class="grid2">
    <div class="card">
      <h3><span class="l">اكتشاف مكان سجل 3uTools</span><button class="btn ghost" id="t-back" style="flex-direction:row">رجوع</button></h3>
      <ol dir="auto" style="padding-inline-start:20px;margin-top:0">
        <li>اضغط «اللقطة الأولى».</li><li>سوِّ الفلاش في 3uTools حتى يظهر الخطأ.</li><li>اضغط «انتهى الفلاش» لتظهر الملفات اللي تغيّرت.</li>
      </ol>
      <div class="acts" style="margin:0"><button class="btn" id="t-start" style="flex-direction:row">اللقطة الأولى</button><button class="btn" id="t-stop" style="flex-direction:row">انتهى الفلاش</button></div>
      <div class="row" style="margin-top:12px;align-items:center;flex-wrap:wrap">
        <input type="text" id="t-extra" dir="ltr" placeholder="مجلد تثبيت 3uTools (مثال: D:\\Program Files\\3uTools)" style="flex:1;min-width:220px">
        <button class="btn ghost" id="t-add">أضف للمسح</button>
      </div>
      <div id="t-dirs" class="note" dir="ltr"></div>
      <div id="t-msg" class="note"></div>
    </div>
    <div class="card"><h3><span class="l">الملفات المتغيّرة</span></h3><div class="list" id="t-list" style="max-height:230px;overflow:auto"></div>
      <textarea id="t-prev" dir="ltr" readonly style="margin-top:10px;min-height:120px" placeholder="اضغط «معاينة» لعرض آخر محتوى الملف"></textarea></div>
  </div>
</section>

<!-- ================= SETTINGS ================= -->
<section class="view" id="v-settings">
  <div class="card" style="max-width:820px">
    <h3><span class="l">الإعدادات</span></h3>
    <label class="mut">مجلد سجلات 3uTools</label>
    <input type="text" id="s-log" dir="ltr"><div id="s-sug" style="margin:6px 0 14px"></div>
    <label class="mut">مجلد المخططات</label>
    <input type="text" id="s-sch" dir="ltr" style="margin-bottom:14px">
    <button class="btn" id="s-save">Save</button>
    <button class="btn ghost" id="s-usb" style="margin-inline-start:8px">تشخيص USB</button>
    <textarea id="s-usbout" dir="ltr" readonly style="margin-top:12px;min-height:160px;display:none"></textarea>
  </div>
</section>
</main>

<footer class="status">
  <span id="st-left"></span><span id="st-mid"></span><span id="st-right"></span>
</footer>
<div class="toast" id="toast"></div>

<script>
const TOKEN="__TOKEN__";
const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)];
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function api(p,body){
  const o={headers:{'X-Token':TOKEN}};
  if(body!==undefined){o.method='POST';o.body=JSON.stringify(body)}
  try{const r=await fetch('/api/'+p,o);return await r.json()}catch(e){return{ok:false,error:'تعذر الاتصال بالبرنامج'}}
}
function toast(t){const e=$('#toast');e.textContent=t;e.style.display='block';clearTimeout(toast.h);toast.h=setTimeout(()=>e.style.display='none',2800)}
const P={
 home:'<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
 usb:'<path d="M12 3v13"/><circle cx="12" cy="19" r="2.5"/><path d="M12 9l4-2v3"/><path d="M12 12l-4-2"/><circle cx="7" cy="9" r="1.2"/><rect x="15" y="6" width="3" height="3"/>',
 act:'<path d="M3 12h4l3-8 4 16 3-8h4"/>',
 dl:'<path d="M12 3v12"/><path d="M7 11l5 5 5-5"/><path d="M4 20h16"/>',
 book:'<path d="M3 5h7a2 2 0 012 2v13a2 2 0 00-2-2H3z"/><path d="M21 5h-7a2 2 0 00-2 2v13a2 2 0 012-2h7z"/>',
 chip:'<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
 tools:'<path d="M14.7 6.3a4 4 0 005 5L10 21a2.1 2.1 0 01-3-3z"/><path d="M14 6l4-3 3 3-3 4"/>',
 gear:'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
 info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
 db:'<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
 cloud:'<path d="M7 18a4 4 0 010-8 5 5 0 019.6-1A4 4 0 0117 18z"/><path d="M12 12v6M9 15l3-3 3 3"/>',
 retry:'<path d="M20 11a8 8 0 10-2.3 5.7"/><path d="M20 4v7h-7"/>',
 copy:'<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M4 16V6a2 2 0 012-2h10"/>',
 save:'<path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
 doc:'<path d="M6 3h9l4 4v14H6z"/><path d="M9 12h7M9 16h7"/>',
 bolt:'<path d="M13 2L5 14h6l-1 8 8-12h-6z"/>'
};
const ic=n=>`<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${P[n]}</svg>`;
const TABS=[['device','USB / الجهاز','معلومات الهاتف','usb'],['baseband','Baseband','سكان البيسباند','chip'],['panic','Restart','أعطال الريستارت','act'],['faults','Fault Library','مكتبة الأعطال','book'],['monitor','Software Monitor','Software Monitor','gear']];
$('#nav').innerHTML=TABS.filter(t=>t[0]!=='faults').map(t=>`<button data-v="${t[0]}">${ic(t[3])}<b>${t[1].split(' / ')[0]}</b><small>${t[2]||t[1].split(' / ')[1]||''}</small></button>`).join('');
const SIDE=[];
$('#side').innerHTML=SIDE.map(s=>s[0]==='dbupdate'?`<button data-dbupdate="1"><span>${s[1]}</span>${ic(s[2])}</button>`:s[0].startsWith('soon')?`<button class="soon" data-soon="1"><span>${s[1]}</span>${ic(s[2])}</button>`:`<button data-v="${s[0]}" class=""><span>${s[1]}</span>${ic(s[2])}</button>`).join('');
function pgHero(v){const t=TABS.find(x=>x[0]===v);let h=$('#pghero');if(!h){h=document.createElement('div');h.id='pghero';h.className='pgh';$('main').insertBefore(h,$('main').firstChild)}
if(!t){h.style.display='none';return}h.style.display='';
h.innerHTML='<svg viewBox="0 0 128 128" fill="none" width="46" height="46" style="flex:none"><rect x="30" y="8" width="68" height="112" rx="16" stroke="#35d0ff" stroke-width="7"/><path d="M50 94V48L78 94V48" stroke="#2f6bff" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/></svg><div><b>'+t[2]+'</b>'+(t[1]!==t[2]?'<small dir="ltr">'+t[1]+'</small>':'')+'</div>'}
function show(v){
  pgHero(v);
  $$('.view').forEach(e=>e.classList.toggle('on',e.id==='v-'+v));
  $$('#nav button').forEach(b=>b.classList.toggle('on',b.dataset.v===v));
  try{location.hash=v}catch(e){}
  if(v==='live'){slStatus()} if(v==='tools')showDirs(); if(v==='schem')loadSchem(); if(v==='faults')loadFaults(); if(v==='settings')loadSettings(); if(v==='monitor')loadFolder(); if(v==='baseband'){blStatus()} 
}
document.addEventListener('click',async e=>{
  if(e.target.closest('[data-dbupdate]')){toast('جاري تحديث جدول الموديلات…');const r=await api('db/update',{});toast(r.ok?'تم تحديث '+r.count+' موديل آيفون':r.error);return}
  const b=e.target.closest('[data-v]'); if(b){show(b.dataset.v);return}
  if(e.target.closest('[data-soon]'))toast('هذه الميزة قيد التطوير');
});

/* ---------- مراقب الفلاش (لوحتان تُكرَّران) ---------- */
const MON_HTML=`
<div class="card">
  <h3><span class="l">${ic('bolt')} Software Monitor (3uTools)</span><span class="mut mon-state"></span></h3>
  <div class="log mon-log"></div>
  <div style="display:flex;gap:10px;align-items:center;margin-top:10px"><span class="mut">Progress :</span><div class="bar" style="flex:1"><i class="mon-bar" style="width:0"></i></div><b class="mon-pct" dir="ltr">0%</b></div>
  <div class="acts">
    <button class="btn" data-a="retry">${ic('retry')}Retry</button>
    <button class="btn ghost" data-a="ipsw">${ic('dl')}Download Firmware</button>
    <button class="btn ghost" data-a="openlog">${ic('doc')}Open 3uTools Log</button>
    <button class="btn ghost" data-a="copy">${ic('copy')}Copy Report</button>
    <button class="btn ghost" data-a="save">${ic('save')}Save Report</button>
  </div>
</div>
<div class="card an">
  <h3><span class="l"><span class="bad">▲</span> Automatic Error Analysis</span></h3>
  <div class="an-body"></div>
</div>
<div class="card stg">
  <h3><span class="l">Flash Stages (from log)</span></h3>
  <div class="stg-body"></div>
</div>`;
$('#d-mon').innerHTML=MON_HTML;$('#m-mon').innerHTML=MON_HTML;
let monTotal=-1,monState=null;
function anHTML(a){
  if(!a){
    if(monState&&monState.success)return`<div class="errbox" style="background:rgba(46,204,113,.18);border-color:#2ecc71;color:#c9f7da">اكتمل الفلاش بنجاح${monState.took?' — <span dir="ltr">'+esc(monState.took)+'</span>':''}</div><div class="empty" dir="auto">لا يوجد خطأ فلاش فعلي. أسطر Error العابرة بعد النجاح لا تُعتبر فشلاً.</div>`;
    if(monState&&monState.end_reached&&!monState.failed)return`<div class="errbox" style="background:rgba(46,204,113,.18);border-color:#2ecc71;color:#c9f7da">وصل الفلاش لآخر مرحلة (Cleaning up)</div><div class="empty" dir="auto">سطر النجاح النهائي يظهر بشريط 3uTools تحت اللوغ ولا يُنسخ مع النص. فشل مراحل داخلية (مثل الباسباند) يظهر في لوحة المراحل.</div>`;
    if(monState&&monState.failed&&!monState.success){const f=monState.failed;return`<div class="errbox">فشل الفلاش${f.after?' عند : <b dir="ltr">'+esc(f.after)+'</b>':''}</div><div class="kvs" style="grid-template-columns:1fr;margin-top:10px"><div style="display:block"><span>رسالة 3uTools :</span><div dir="ltr" style="font-weight:600;margin-top:2px;color:#ff4d5e">${esc(f.text)}</div></div></div><div class="empty" dir="auto">هذا نص السجل حرفياً. السبب الفعلي غير مذكور في السجل، فلا يوجد تفسير مضاف.</div>`;}
    return'<div class="empty">No error detected yet.<br>Start a flash in 3uTools or run Demo.</div>';
  }
  return`<div class="errbox">الخطأ المكتشف : <b dir="ltr">${esc(a.code)}</b></div>
  <div class="kvs" style="grid-template-columns:1fr;margin-top:10px">
   <div><span>مرحلة الفشل :</span><span class="v">${esc(a.stage)}</span></div>
   <div><span>الفئة :</span><span class="v" dir="auto">${esc(a.cat_ar)}</span></div>
   <div style="display:block"><span>السبب المحتمل :</span><div dir="auto" style="font-weight:600;margin-top:2px">${esc(a.cause)}</div></div></div>
  ${(a.poss||[]).length?`<h4>الاحتمالات الفنية :</h4><ol dir="auto">${a.poss.map(x=>`<li>${esc(x)}</li>`).join('')}</ol>`:''}
  ${(a.checks||[]).length?`<div class="fix"><h4 style="margin-top:0;color:var(--ok)">خطوات الفحص والإصلاح :</h4>${a.checks.map(x=>`<label dir="auto"><input type="checkbox"><span>${esc(x)}</span></label>`).join('')}</div>`:''}`;
}
const CAT_AR2={MEM:'الذاكرة / التخزين',BB:'البيسباند',FW:'فيرموير القطع',SYS:'النظام والإقلاع',OTH:'أخرى'};
const fT=s=>s?esc(String(s).slice(0,8)):'—';
const _sec=t=>{const p=String(t).split(':');return p.length<3?null:(+p[0])*3600+(+p[1])*60+parseFloat(p[2])};
const _dur=(a,b)=>{const x=_sec(a),y=_sec(b);return x===null||y===null?null:(y-x+86400)%86400};
function tlHTML(m){
  const tl=m.timeline||[]; if(!tl.length&&!m.failed)return'';
  let h='';
  if(m.failed&&!m.success){
    const f=m.failed;
    h+='<div class="note" dir="auto" style="border-inline-start:3px solid #ff4d5e;padding:6px 10px;margin:8px 0"><b style="color:#ff4d5e">فشل الفلاش</b>'
      +(f.after?'<div>آخر عملية قبل الفشل: <span dir="ltr">'+esc(f.after)+'</span> <span class="mut">('+fT(f.after_t)+')</span></div>':'')
      +'<div>رسالة 3uTools: <span dir="ltr" style="color:#ff4d5e">'+esc(f.text)+'</span> <span class="mut">('+fT(f.t)+')</span></div>'
      +(f.after_t&&f.t?'<div class="mut">المدة بين آخر عملية والخطأ: <span dir="ltr">'+(_dur(f.after_t,f.t)||0).toFixed(1)+' s</span></div>':'')
      +'<div class="mut">السبب الفعلي هو ما تقوله رسالة 3uTools حرفياً، ولا أضيف تفسيراً من عندي.</div></div>';
  }
  if(!m.success&&!m.failed&&!m.end_reached&&tl.length){
    const la=m.last_action,op=m.open||[],tail=m.tail||[];
    h+='<div class="note" dir="auto" style="border-inline-start:3px solid #f5b942;padding:6px 10px;margin:8px 0"><b>مكان التوقف / الحالة الحالية</b>'
      +(la?'<div>آخر عملية: <span dir="ltr">'+esc(la.text)+'</span> <span class="mut">('+fT(la.t)+')</span></div>':'')
      +(op.length?'<div>مراحل بدأت ولم تنتهِ: <span dir="ltr">'+op.slice(-4).map(e=>esc(e.name)+' ('+fT(e.start)+')').join(' ، ')+'</span></div>':'')
      +(tail.length?'<div class="mut" style="margin-top:4px">آخر أسطر السجل كما وردت:</div><pre dir="ltr" style="white-space:pre-wrap;font-size:11.5px;margin:4px 0">'+esc(tail.slice(-8).join('\n'))+'</pre>':'')
      +'<div class="mut">إذا توقف الفلاش هنا فالسبب هو ما تقوله هذه الأسطر حرفياً، ولا أضيف تفسيراً من عندي.</div></div>';
  }
  if(!m.success&&m.hw&&m.hw.length){
    h+='<div dir="rtl" style="margin:8px 0;border-inline-start:3px solid #2ecc71;padding:8px 12px;background:rgba(46,204,113,.08)"><b style="color:#2ecc71">الحل: افحص هذه الخطوط</b>'
      +'<div class="mut" style="font-size:12px;margin:2px 0 6px">الفلاش توقف عند نفس المرحلة التي ظهرت في اختبارك أنت على جهاز واحد. هذا اتجاه للفحص وليس حكماً.</div>'
      +m.hw.map(x=>'<div dir="ltr" style="margin-top:6px"><a href="#" data-hwnet="'+esc(x.net)+'" style="font-size:14px;font-weight:700;color:inherit;text-decoration:underline;cursor:pointer">'+esc(x.net)+'</a> <span class="mut" style="font-size:12px">('+esc(x.model)+')</span>'+(x.fix?'<div dir="rtl" style="margin-top:3px;font-size:13.5px">'+esc(x.fix)+'</div>':'')+'</div>').join('')+'</div>';
  }
  const cd=m.cond||{},cdk=[['product','Model'],['mode','Start mode'],['engine','Engine'],['fw','Firmware'],['build','Build'],['variant','Variant'],['behavior','Restore behavior']];
  const cdv=cdk.map(([k,l])=>{const v=k==='product'?m.product:cd[k];return v?'<span class="mut">'+l+':</span> <b>'+esc(v)+'</b>':''}).filter(Boolean);
  if(cdv.length)h+='<div dir="ltr" style="margin:6px 0;font-size:12.5px"><b>Flash conditions (as written in the log):</b> '+cdv.join(' &nbsp;|&nbsp; ')+'</div>';
  const RF=m.ref&&m.ref.stages?m.ref.stages:null;
  if(tl.length){h+=RF?'<div class="mut" dir="ltr" style="margin:6px 0">Healthy reference: '+esc(m.ref.name)+' — total '+esc(m.ref.total)+'. Column <b>Ref sec</b> = stage duration on the healthy phone.</div>':'<div class="mut" dir="ltr" style="margin:6px 0">No healthy flash reference stored for this model'+(m.product?' ('+esc(m.product)+')':'')+'.</div>';}
  ['MEM','BB','FW','SYS','OTH'].forEach(c=>{
    const rows=tl.filter(e=>e.cat===c); if(!rows.length)return;
    h+='<details style="margin:6px 0"'+(c==='MEM'||c==='BB'?' open':'')+'><summary><b>'+CAT_AR2[c]+'</b> <span class="mut">('+rows.length+')</span></summary><table class="tlt" dir="ltr"><tr><th style="text-align:left">Stage</th><th>Start</th><th>End</th><th>Sec</th>'+(RF?'<th>Ref sec</th>':'')+'</tr>'
      +rows.map(e=>'<tr style="'+(e.end===null?'background:rgba(245,185,66,.15);':'')+'"><td style="text-align:left">'+esc(e.name)+'</td><td>'+fT(e.start)+'</td><td>'+(e.end===null?'…':fT(e.end))+'</td><td>'+(e.dur===null||e.dur===undefined?'':e.dur.toFixed(1))+'</td>'+(RF?'<td>'+(RF[e.name]&&RF[e.name][1]!==null?RF[e.name][1].toFixed(1):'')+'</td>':'')+'</tr>').join('')+'</table></details>';
  });
  const as=m.asrsteps||[];
  if(as.length>1){
    h+='<details style="margin:6px 0"><summary><b>كتابة نظام الملفات (ASR)</b> <span class="mut">سرعة كل خطوة</span></summary><table class="tlt" dir="ltr"><tr><th style="text-align:left">Sending</th><th>Time</th><th>Sec since previous</th></tr>'
      +as.map(x=>'<tr style="'+(x.slow?'background:rgba(255,77,94,.18);':'')+'"><td style="text-align:left">'+x.pct+'%</td><td>'+fT(x.t)+'</td><td>'+(x.d===null||x.d===undefined?'':x.d.toFixed(1))+(x.slow?' ⚠':'')+'</td></tr>').join('')+'</table><div class="mut" dir="auto" style="font-size:12px">⚠ = الخطوة أخذت أكثر من ضعف الوسيط. هذا قياس زمني فقط وليس تشخيصاً.</div></details>';
  }
  return h;
}
function stgHTML(m){
  const st=m.stages||[],bb=m.bb||{};
  if(!st.length&&!bb.rejects&&!bb.updating&&!(m.fails||[]).length)return tlHTML(m)||'<div class="empty">No stages yet. Start a flash while the monitor is running.</div>';
  const isBB=n=>/baseband|modem/i.test(n);
  let h='';
  const fl=m.fails||[],wl=m.warns||[];
  if(fl.length||wl.length){
    h+='<div dir="ltr" style="font-size:12.5px;margin-bottom:10px">';
    h+=fl.map(f=>{const cont=(m.timeline||[]).some(e=>e.start>(f.t||'~'));return'<div style="border-inline-start:3px solid #ff4d5e;padding:4px 8px;margin:4px 0;background:rgba(255,77,94,.12)"><b>FAILURE '+esc(f.id)+' '+esc(f.name||'')+'</b> result='+esc(f.res)+' <span class="mut">'+esc(f.t||'')+'</span><br>'+esc(f.detail||'')+(f.note?'<div dir="rtl" style="margin:4px 0;padding:4px 8px;border-inline-start:3px solid #2ecc71;background:rgba(46,204,113,.08);text-align:right;font-size:13.5px">'+esc(f.note.note)+'<div class="mut" style="font-size:11.5px">'+esc(f.note.src)+'</div></div>':'')+'<br><span class="mut">'+(cont?'The flash continued with the next stages after this failure.':'No later stage appears in the log after this failure.')+'</span></div>'}).join('');
    h+=wl.map(f=>'<div style="border-inline-start:3px solid #f5b942;padding:4px 8px;margin:4px 0"><b>WARNING '+esc(f.id)+'</b> <span class="mut">'+esc(f.t||'')+'</span><br>'+esc(f.detail||'')+'</div>').join('');
    h+='</div>';
  }
  if(bb.rejects||bb.updating||bb.sending||bb.version){
    h+='<div class="kvs" style="grid-template-columns:1fr" dir="auto">';
    h+='<div><span>Updating Baseband :</span><span class="v">'+bb.updating+' مرة</span></div>';
    h+='<div><span>Sending BasebandData :</span><span class="v">'+bb.sending+' مرة</span></div>';
    h+='<div><span>device didn\'t accept BasebandData :</span><span class="v" style="color:'+(bb.rejects?'var(--bad,#ff4d5e)':'inherit')+'">'+bb.rejects+' مرة</span></div>';
    if(bb.version)h+='<div><span>Baseband Version :</span><span class="v" dir="ltr">'+esc(bb.version)+'</span></div>';
    h+='</div>';
    if(bb.rejects)h+='<div class="note" dir="auto">ادّعاء من موقع to-check.com (أداة BASEBAND CHECK) وشرح قناة indo-apple على يوتيوب: تكرار هذا الخطأ يعني أن الـ EEPROM يستجيب وأن هناك اتصالاً بين الباسباند والمعالج. هذا ليس مصدراً رسمياً من Apple ولا يمكن التحقق منه هنا، ولا ينطبق إذا كان الباسباند غير مركّب على البورد.</div>';
  }
  if(st.length){
    h+='<table style="width:100%;font-size:12.5px;margin-top:8px" dir="ltr"><tr><th style="text-align:left">Stage</th><th>ID</th><th>Result</th><th>Starts</th></tr>'+st.map(e=>{
      const bad=e.done&&e.res!==null&&e.res!=='0',hl=isBB(e.name);
      return'<tr style="'+(bad?'background:rgba(255,77,94,.18);':'')+'"><td style="text-align:left">'+(hl?'● ':'')+esc(e.name)+'</td><td>'+esc(e.id)+'</td><td>'+(e.done?(e.res===null?'done':esc(e.res)):'running…')+'</td><td>'+e.starts+'</td></tr>'}).join('')+'</table>';
  }
  return tlHTML(m)+h;
}
function renderMon(m){
  monState=m;
  const st=m.running?`<span class="dot"></span>Monitoring…`:`<span class="dot off"></span>Stopped`;
  $$('.mon-state').forEach(e=>e.innerHTML=st);
  $$('.mon-bar').forEach(e=>e.style.width=m.progress+'%');$$('.mon-pct').forEach(e=>e.textContent=m.progress+'%');
  if(m.total!==monTotal||m.reset){
    monTotal=m.total;
    $$('.mon-log').forEach(b=>{b.innerHTML=(m.all||[]).map(l=>`<div class="ln ${l.tag==='Error'?'err':''}"><span class="t">[${l.t}]</span><span class="tag ${l.tag==='3uTools'?'c3':l.tag}">${l.tag}</span><span>${esc(l.msg)}</span></div>`).join('');b.scrollTop=b.scrollHeight});
    $$('.an-body').forEach(b=>b.innerHTML=anHTML(m.analysis));
    $$('.stg-body').forEach(b=>b.innerHTML=stgHTML(m));
    $('#h-err').innerHTML=m.analysis?`<div class="stat bad" dir="ltr">${esc(m.analysis.code)}</div><div dir="auto">${esc(m.analysis.cause)}</div>`:(m.success?'اكتمل الفلاش بنجاح'+(m.took?' — <span dir="ltr">'+esc(m.took)+'</span>':''):'لا يوجد خطأ حتى الآن.');
  }
}
let lines=[],monSince=0;
async function pollMon(){
  const m=await api('monitor?since='+monSince);
  if(m.error&&!m.lines)return;
  if(m.reset)lines=[]; lines=lines.concat(m.lines).slice(-600); monSince=m.total; m.all=lines.slice(-300); renderMon(m);
}
function report(){
  const a=monState&&monState.analysis; if(!a)return'';
  return `Nawaf Team v1.8 — تقرير\nالتاريخ: ${new Date().toLocaleString()}\nالخطأ: ${a.code}\nمرحلة الفشل: ${a.stage}\nالفئة: ${a.cat_ar}\nالسبب المحتمل: ${a.cause}\nالاحتمالات:\n${a.poss.map((x,i)=>' '+(i+1)+'. '+x).join('\n')}\nخطوات الفحص:\n${a.checks.map(x=>' - '+x).join('\n')}\nالسطر الأصلي: ${a.line}\n`;
}
document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-a]'); if(!b)return; const a=b.dataset.a;
  if(a==='retry'){await api('monitor/reset',{});lines=[];monTotal=-1;monSince=0;const f=$('#m-folder').value;const r=await api('monitor/start',{folder:f});if(!r.ok)toast(r.error||'تم تصفير اللوحة')}
  if(a==='ipsw')window.open('https://ipsw.me','_blank');
  if(a==='openlog'){const r=await api('openlog',{});if(!r.ok)toast(r.error)}
  if(a==='copy'){const t=report();if(!t)return toast('لا يوجد تقرير بعد');try{await navigator.clipboard.writeText(t);toast('تم النسخ')}catch(_){const x=document.createElement('textarea');x.value=t;document.body.appendChild(x);x.select();document.execCommand('copy');x.remove();toast('تم النسخ')}}
  if(a==='save'){const t=report();if(!t)return toast('لا يوجد تقرير بعد');const l=document.createElement('a');l.href=URL.createObjectURL(new Blob([t],{type:'text/plain;charset=utf-8'}));l.download='nawaf_report_'+Date.now()+'.txt';l.click()}
});
async function loadFolder(){const s=await api('settings');if(!$('#m-folder').value)$('#m-folder').value=s.log_dir||(s.suggest&&s.suggest[0])||''}
$('#m-start').onclick=async()=>{lines=[];monTotal=-1;monSince=0;const r=await api('monitor/start',{folder:$('#m-folder').value});if(!r.ok)toast(r.error);else toast('المراقبة شغالة — ابدأ الفلاش')};
$('#m-win').onclick=async()=>{lines=[];monTotal=-1;monSince=0;const r=await api('monitor/winstart',{});if(!r.ok)toast(r.error);else toast('قراءة تلقائية من نافذة 3uTools — ابدأ الفلاش')};
$('#m-winprobe').onclick=async()=>{const el=$('#m-paste');el.value='انتقل الآن إلى نافذة 3uTools واتركها ظاهرة أمامك (غير مصغّرة) — سيتم التصوير بعد 8 ثوانٍ، ثم ارجع هنا وانتظر النتيجة…';let r;try{r=await api('monitor/winread',{delay:8})}catch(e){el.value='فشل الطلب: '+e;return}el.value='[تشخيص] عناصر نصية: '+r.items+' | أسطر سجل: '+(r.text?r.text.split('\n').length:0)+'\n[تفاصيل] '+(r.err||'-')+'\n\n'+(r.text||'')}
$('#m-stop').onclick=()=>api('monitor/stop',{});

let FLP='';
$('#fl-pick').onclick=async()=>{const r=await api('flash/pick',{});if(r.path)$('#fl-file').value=r.path;else toast('ما انختار ملف (ممكن تلصق المسار يدوياً)')};
$('#fl-list').onclick=async()=>{$('#fl-items').textContent='جاري الجلب…';const r=await api('flash/list',{});if(!r.ok){$('#fl-items').textContent=r.error;return}FLP=r.product;$('#fl-items').innerHTML='<div class="mut" dir="ltr">'+esc(r.product)+'</div>'+r.items.slice(0,15).map((x,i)=>'<div class="it" style="display:flex;gap:8px;align-items:center;justify-content:space-between" dir="auto"><span dir="ltr">iOS '+esc(x.version)+' ('+esc(x.build)+') · '+(x.size?Math.round(x.size/1048576)+' MB':'')+' · '+esc(x.date)+'</span><span>'+(x.signed?'<b style="color:#2ecc71">موقّع</b>':'<span class="mut">غير موقّع</span>')+'</span><button class="btn ghost" data-fd="'+esc(x.url)+'">تنزيل</button></div>').join('')};
document.addEventListener('click',async e=>{const b=e.target.closest('[data-fd]');if(!b)return;const r=await api('flash/download',{url:b.dataset.fd});if(!r.ok)toast(r.error);else{$('#fl-file').value=r.path;toast('بدأ التنزيل')}});
$('#fl-go').onclick=async()=>{const f=$('#fl-file').value.trim();if(!f){toast('اختر ملف IPSW أولاً');return}if(!confirm('الاستعادة بتمسح بيانات الهاتف وبتبدأ الآن. تابع؟'))return;lines=[];monTotal=-1;monSince=0;const r=await api('flash/start',{ipsw:f,erase:$('#fl-erase').checked,confirm:true});if(!r.ok)toast(r.error)};
$('#fl-stop').onclick=async()=>{await api('flash/stop',{})};
async function flPoll(){if(!$('#v-monitor').classList.contains('on'))return;const s=await api('flash/status');if(!s||s.state===undefined)return;$('#fl-tool').textContent=s.tool?'idevicerestore ready':'idevicerestore not found';let t='';if(s.dl&&s.dl.state!=='idle')t+='التنزيل: '+(s.dl.msg||'')+(s.dl.pct?' ('+s.dl.pct+'%)':'')+' — ';if(s.state!=='idle')t+=s.msg+(s.pct?' ('+s.pct+'%)':'');$('#fl-msg').textContent=t}
setInterval(flPoll,2000);
$('#m-demo').onclick=async()=>{lines=[];monTotal=-1;monSince=0;await api('monitor/demo',{});toast('محاكاة تجريبية')};
$('#m-scanbtn').onclick=async()=>{const o=$('#m-scan');o.innerHTML='<span class="mut">جارٍ قراءة سجل الجهاز وتقارير الأعطال… لا تفصل الجهاز.</span>';const r=await api('modem/scan',{});
  if(!r.ok){o.innerHTML='<span style="color:#ff8a8a">'+esc(r.error||'فشل الفحص')+'</span>';return}
  const li=a=>a&&a.length?'<ul style="margin:4px 0;padding-inline-start:18px">'+a.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>':'';
  const sl=r.syslog||{};let h='';
  if(sl.available){h+=`<div><b>سجل الجهاز:</b> ${sl.total} سطر (${esc(sl.source||'')}) | مرتبط بالمودم: ${sl.kw} | فيه أخطاء: ${sl.bad}</div>`;
    if(sl.bad_lines&&sl.bad_lines.length)h+='<div class="mut" style="margin-top:6px">أسطر أخطاء المودم:</div><pre dir="ltr" style="white-space:pre-wrap;max-height:160px;overflow:auto;margin:4px 0;font-size:11.5px">'+esc(sl.bad_lines.join('\n'))+'</pre>';
    else if(sl.sample&&sl.sample.length)h+='<div class="mut" style="margin-top:6px">عيّنة من نشاط المودم:</div><pre dir="ltr" style="white-space:pre-wrap;max-height:120px;overflow:auto;margin:4px 0;font-size:11.5px">'+esc(sl.sample.join('\n'))+'</pre>'}
  else h+=`<div style="color:#ff8a8a">${esc(sl.error||'سجل الجهاز غير متاح')}</div>`;
  const io=r.ioreg||{};if(io.available)h+='<div style="margin-top:8px"><b>سجل عتاد النظام (IORegistry):</b> '+(io.tokens.length?io.tokens.length+' عنصر مرتبط بالمودم':'لا يوجد عنصر مرتبط بالمودم')+'</div>'+(io.tokens.length?'<pre dir="ltr" style="white-space:pre-wrap;max-height:100px;overflow:auto;margin:4px 0;font-size:11.5px">'+esc(io.tokens.join('\n'))+'</pre>':'');
  if(r.panics&&r.panics.length){h+='<div style="margin-top:8px"><b>تقارير panic على الجهاز:</b></div>'+r.panics.map(p=>pnLink(p)).join('')}
  else h+='<div class="mut" style="margin-top:8px">لا توجد تقارير panic'+(r.pull_ok?'':' (تعذر سحبها)')+'.</div>';
  if(r.findings&&r.findings.length)h+='<div style="margin-top:8px"><b>الخلاصة (تقديرية):</b></div>'+li(r.findings);
  if(r.steps&&r.steps.length)h+='<div style="margin-top:8px"><b>شو نعمل:</b></div>'+li(r.steps);
  o.innerHTML=h};
$('#m-pasteGo').onclick=async()=>{const t=$('#m-paste').value;if(!t.trim())return toast('الصق نص السجل أولاً');lines=[];monTotal=-1;monSince=0;await api('monitor/analyze',{text:t});toast('تم تحليل النص')};
$('#m-pasteClr').onclick=()=>{$('#m-paste').value=''};
$('#m-file').onchange=async e=>{const f=e.target.files[0];if(!f)return;lines=[];monTotal=-1;monSince=0;await api('monitor/analyze',{text:await f.text()});toast('تم تحليل الملف');e.target.value=''};

/* ---------- الجهاز ---------- */
let dev=null;
async function pollDev(){
  dev=await api('device'); const d=dev;
  $('#d-demo').style.display=d.demo?'':'none';
  if(!d.connected){
    $('#d-name').textContent='لا يوجد جهاز'; $('#d-status').innerHTML=`<span class="bad">● Not connected</span>`;
    document.body.classList.add('nodev');    $('#d-kvs').innerHTML='';$('#b-pct').textContent='—';$('#b-fill').style.width='0';$('#s-txt').textContent='—';$('#s-fill').style.width='0';$('#d-kvs').innerHTML='<div class="nodh" dir="rtl">وصّل الهاتف بالكابل وافتح شاشته ووافق على «الوثوق بهذا الكمبيوتر»</div>';$('#b-cyc').textContent='—';$('#b-health').textContent='—';
    $('#d-modes').innerHTML='';$('#m-card').style.display='none';$('#m-body').innerHTML='';
    $('#h-dev').innerHTML=`<span class="bad">غير متصل</span>`;
    $('#st-left').innerHTML='<span class="dot off"></span>Disconnected';$('#st-mid').textContent='';return;
  }
  document.body.classList.remove('nodev');  $('#d-name').textContent=d.name;
  $('#d-status').innerHTML=`<span class="ok">✔ Connected – ${esc(d.mode)} Mode</span>`+(d.note?` <span class="mut" dir="auto" style="font-size:12.5px">${esc(d.note)}</span>`:'');
  const f=d.fields,r=d.right,L=Object.entries(f).map(([k,v])=>`<div><span>${k} :</span><span class="v">${esc(v)}</span></div>`);
  const R=[['لون الجهاز',r.color],['السعة',r.capacity],['حالة Find My',r.findmy,r.findmy==='مفعّل'],['حالة Activation',r.activation,r.activation==='مفعّل'||r.activation==='Activated'],['حالة الجيلبريك',r.jb],['وضع الجهاز',r.mode,1]]
    .map(x=>`<div><span dir="rtl">${x[0]} :</span><span class="v ${x[2]?'ok':''}" dir="auto">${esc(x[1])}</span></div>`);
  // اجعل الأعمدة: يسار=الحقول التقنية، يمين=الحالة
  $('#d-kvs').innerHTML=L.map((x,i)=>x+(R[i]||'<div></div>')).join('')+R.slice(L.length).join('');
  const b=d.battery;
  if(b){$('#b-pct').textContent=b.pct+'%';$('#b-fill').style.width=b.pct+'%';$('#b-cyc').textContent=b.cycles??'—';$('#b-health').textContent=b.health?b.health+'%':'—'}
  else{$('#b-pct').textContent='—';$('#b-fill').style.width='0';$('#b-cyc').textContent='—';$('#b-health').textContent='—'}
  const s=d.storage; if(s){$('#s-fill').style.width=(100*s.used/s.total)+'%';$('#s-txt').textContent=`${s.used} GB / ${Math.round(s.total)} GB (${Math.round(100*s.used/s.total)}%)`}else{$('#s-fill').style.width='0';$('#s-txt').textContent='—'}
  const mo=d.modem,mc=$('#m-card');
  if(mo){mc.style.display='';const col=mo.state==='ok'?'ok':'';const li=a=>a&&a.length?'<ul dir="rtl" style="margin:6px 0 0;padding-inline-start:18px;font-size:12.5px">'+a.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>':'';
    $('#m-body').innerHTML=`<div class="${col}" dir="rtl" style="font-weight:600;${mo.state==='ok'?'':'color:#ff8a8a'}">${esc(mo.title)}</div>
    <div class="kvs" style="grid-template-columns:1fr;margin-top:6px"><div><span>Baseband :</span><span class="v" dir="ltr">${esc(mo.baseband)}</span></div><div><span>IMEI :</span><span class="v" dir="ltr">${esc(mo.imei)}</span></div><div><span>SIM :</span><span class="v" dir="ltr">${esc(mo.sim)}</span></div></div>`
    +(mo.poss.length?`<div dir="rtl" class="mut" style="margin-top:8px">الأسباب المحتملة (تقديرية):</div>${li(mo.poss)}<div dir="rtl" class="mut" style="margin-top:8px">خطوات الفحص:</div>${li(mo.checks)}`:'')}
  else mc.style.display='none';
  $('#d-modes').innerHTML=['Normal','Recovery','DFU','Restore'].map(m=>`<span class="btn ${d.mode.toLowerCase().startsWith(m.toLowerCase())?'on':''}">${m} Mode</span>`).join('');
  $('#h-dev').innerHTML=`<div class="stat">${esc(d.name)}</div><div class="ok">${esc(d.mode)} Mode</div><div class="mut" dir="ltr">${esc(f['iOS Version'])}</div>`;
  $('#st-left').innerHTML=`<span class="dot"></span>Connected`;$('#st-mid').innerHTML=`${esc(d.name)} (${esc(f['Model Identifier'])}) &nbsp;|&nbsp; iOS ${esc(f['iOS Version'].split(' ')[0])} &nbsp;|&nbsp; ECID: ${esc(f.ECID)}`;
}


/* ---------- Baseband Analysis ---------- */
let bbTimer=null,bbLoaded=false;
function bbLi(a){return a&&a.length?'<ul style="margin:4px 0;padding-inline-start:18px">'+a.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>':''}
async function loadBB(){
  if(!bbLoaded){const m=await api('bb/models');const sel=$('#bb-model');sel.innerHTML=(m.models||[]).map(x=>`<option value="${esc(x.pt)}">${esc(x.name)} — ${esc(x.pt)}</option>`).join('');bbLoaded=true}
  const d=await api('device');
  if(d.connected&&d.fields){const pt=d.fields['Model Identifier'];if(pt&&![...$('#bb-model').options].some(o=>o.value===pt)){const o=document.createElement('option');o.value=pt;o.textContent=(d.name&&d.name!==pt?d.name+' — ':'')+pt;$('#bb-model').appendChild(o)}if(pt)$('#bb-model').value=pt;$('#bb-dev').textContent=d.name+' — '+d.mode}
  else $('#bb-dev').textContent='لا يوجد جهاز متصل';
  loadBBNotes();bbPoll();
}

/* ---- قياس موجّه ---- */
let FL=null,flPath=[],flCur=null;
async function loadFlow(pt,verdict){
  const c=$('#fl-card');
  if(!pt){c.style.display='none';FL=null;return}
  const r=await api('flow/get',{pt});
  if(!r.ok){c.style.display='none';FL=null;return}
  FL=r;FL.pt=pt;c.style.display='';$('#fl-topic').textContent=r.name+' — '+r.topic;$('#fl-src').textContent=(verdict?'التشخيص: '+verdict+' — ':'')+'المصدر: '+r.src;
  flPath=[];flCur=r.start;flRender();
}
function flRender(){
  const b=$('#fl-body');if(!FL){b.innerHTML='';return}
  let h='';
  flPath.forEach((st,i)=>{h+=`<div class="mut" style="margin:4px 0">✔ ${esc(st.point)}: <b>${esc(st.choice)}</b>${st.value?` — قراءتي: ${esc(st.value)}`:''}</div>`});
  const n=FL.nodes[flCur];
  if(n.result){
    const col=n.kind==='step'?'#ffd479':'#9ec7ff';
    h+=`<div style="border:1px solid ${col};border-radius:10px;padding:12px;margin-top:8px"><div style="font-weight:700;color:${col}">${esc(n.title)}</div><div style="margin-top:6px;line-height:1.7">${esc(n.text)}</div></div>`;
    h+=`<textarea id="fl-note" dir="rtl" placeholder="ملاحظة اختيارية (رقم السيريال، ما فعلته…)" style="margin-top:8px;width:100%;min-height:50px"></textarea>`;
    h+=`<div class="row" style="margin-top:8px"><button class="btn" id="fl-save">Save Session</button><button class="btn ghost" id="fl-back">Back</button><button class="btn ghost" id="fl-reset">Restart</button></div>`;
  }else{
    const img=FL.images&&FL.images[flCur];
    h+=`<div style="border:1px solid var(--line);border-radius:10px;padding:12px;margin-top:8px"><div style="font-weight:700">${esc(n.title)}</div>`;
    h+=`<div class="mut" dir="ltr" style="margin:6px 0">النقطة: <b>${esc(n.point)}</b> &nbsp; المتوقع: <b>${esc(n.expect)}</b></div>`;
    if(img)h+=`<img src="${img}" style="max-width:100%;border-radius:8px;margin:6px 0">`;
    h+=`<input id="fl-val" dir="ltr" placeholder="القراءة الفعلية (اختياري، مثل 1.78)" style="margin:6px 0;width:100%;background:#071426;border:1px solid var(--line);border-radius:10px;padding:8px 10px;color:inherit">`;
    h+=`<div class="row" style="gap:8px;flex-wrap:wrap">`+n.opts.map((o,i)=>`<button class="btn ghost fl-opt" data-i="${i}" style="flex:1;min-width:140px">${esc(o.label)}</button>`).join('')+`</div>`;
    h+=`<div class="row" style="margin-top:8px;gap:8px"><label class="btn ghost" style="cursor:pointer">أرفق صورة اللوحة<input type="file" accept="image/*" id="fl-img" style="display:none"></label>`+(img?`<button class="btn ghost" id="fl-imgdel">Delete Image</button>`:'')+(flPath.length?`<button class="btn ghost" id="fl-back">Back</button>`:'')+`</div></div>`;
  }
  b.innerHTML=h;
  b.querySelectorAll('.fl-opt').forEach(el=>el.onclick=()=>{const n=FL.nodes[flCur],o=n.opts[+el.dataset.i];flPath.push({node:flCur,point:n.point,choice:o.label,value:($('#fl-val').value||'').trim()});flCur=o.next;flRender()});
  const back=$('#fl-back');if(back)back.onclick=()=>{const l=flPath.pop();if(l)flCur=l.node;flRender()};
  const rs=$('#fl-reset');if(rs)rs.onclick=()=>{flPath=[];flCur=FL.start;flRender()};
  const sv=$('#fl-save');if(sv)sv.onclick=async()=>{const r=await api('flow/save',{pt:FL.pt,steps:flPath,result:FL.nodes[flCur].title,note:$('#fl-note').value});toast(r.ok?'تم حفظ الجلسة ('+r.count+')':'فشل الحفظ')};
  const fi=$('#fl-img');if(fi)fi.onchange=()=>{const f=fi.files[0];if(!f)return;const rd=new FileReader();rd.onload=async()=>{
    const im=new Image();im.onload=async()=>{const k=Math.min(1,1100/im.width),cv=document.createElement('canvas');cv.width=im.width*k;cv.height=im.height*k;cv.getContext('2d').drawImage(im,0,0,cv.width,cv.height);
      const d=cv.toDataURL('image/jpeg',.82);const r=await api('flow/img',{pt:FL.pt,node:flCur,data:d});if(r.ok){FL.images=FL.images||{};FL.images[flCur]=d;flRender()}else toast(r.error||'فشل')};im.src=rd.result};rd.readAsDataURL(f)};
  const fd=$('#fl-imgdel');if(fd)fd.onclick=async()=>{await api('flow/img',{pt:FL.pt,node:flCur,data:''});delete FL.images[flCur];flRender()};
}
async function loadBBNotes(){const pt=$('#bb-model').value;if(!pt)return;const r=await api('bb/notes/get',{pt});$('#bb-notes').value=r.text||'';$('#bb-notes-card').style.display=''}
$('#bb-model').onchange=loadBBNotes;
$('#bb-notesave').onclick=async()=>{await api('bb/notes',{pt:$('#bb-model').value,text:$('#bb-notes').value});toast('تم حفظ الملاحظات')};
$('#bb-basesave').onclick=async()=>{if(!confirm('احفظ هذا الجهاز كمرجع سليم؟ استعمل جهازاً تأكدت أن المودم فيه سليم.'))return;const r=await api('bb/baseline',{});toast(r.ok?('تم الحفظ لـ '+r.pt+' ('+r.count+' مفتاح)'):(r.error||'فشل الحفظ'))};
$('#bb-go').onclick=async()=>{const rs=$('#bb-restart').checked;if(rs&&!confirm('سيُعاد تشغيل الهاتف الآن. تابع؟'))return;const r=await api('bb/start',{model:$('#bb-model').value,restart:rs,secs:+$('#bb-secs').value});if(!r.ok){toast(r.error||'تعذر البدء');return}$('#bb-res').style.display='none';$('#fl-card').style.display='none';bbPoll()};
async function bbPoll(){
  clearTimeout(bbTimer);const s=await api('bb/status');
  const pg=$('#bb-prog');if(s.state==='running'){pg.style.display='';pg.innerHTML=`<div dir="rtl" style="display:flex;align-items:center;gap:14px;border:1px solid var(--cy);border-radius:10px;padding:12px"><span class="gear">⚙</span><div style="flex:1"><div style="font-size:26px;font-weight:800" dir="ltr">${s.pct||0}%</div><div class="bar"><i style="width:${s.pct||0}%"></i></div><div class="mut" style="margin-top:4px">${esc(s.stage||'')}</div></div></div>`}else{pg.style.display='none';pg.innerHTML=''}
  $('#bb-go').disabled=s.state==='running';
  if(s.state==='running'){bbTimer=setTimeout(bbPoll,1000);return}
  if(s.state==='done'&&s.result)bbRender(s.result);
  else if(s.state==='error'){const o=$('#bb-res');o.style.display='';o.innerHTML='<span style="color:#ff8a8a">'+esc((s.result&&s.result.error)||s.stage||'فشل التحليل')+'</span>'}
}

/* ---- تحليل ملف سجل ---- */
function bbLogInit(){
  const fi=$('#bb-logfile');if(!fi)return;
  fi.onchange=()=>{const f=fi.files[0];if(!f)return;const o=$('#bb-fres');o.style.display='';o.innerHTML='<div class="mut">جارٍ قراءة الملف…</div>';
    const rd=new FileReader();rd.onload=async()=>{const r=await api('bb/logfile',{text:rd.result,name:f.name});
      if(!r.ok){o.innerHTML='<div style="color:#ff8a8a">'+esc(r.error||'فشل')+'</div>';return}
      const vc={ok:'#35e08a',suspect:'#ffd479'}[r.verdict.level]||'#9ec7ff',sl=r.syslog;
      let h=`<div style="border:1px solid ${vc};border-radius:10px;padding:10px"><div class="mut" style="font-size:12px">نتيجة تحليل الملف: <span dir="ltr">${esc(r.name)}</span></div><div style="font-weight:700;color:${vc}">${esc(r.verdict.label)}</div></div>`;
      h+=`<div style="margin-top:6px">${sl.total} سطر | مرتبط بالمودم: ${sl.kw} | فيه أخطاء: ${sl.bad}${sl.tagged?` | سطور Error عامة: ${sl.tagged}`:''}</div>`+bbLi(r.findings);
      const ls=(sl.bad_lines&&sl.bad_lines.length)?sl.bad_lines:(sl.sample||[]);if(ls.length)h+='<pre dir="ltr" style="white-space:pre-wrap;max-height:160px;overflow:auto;font-size:11.5px">'+esc(ls.join('\n'))+'</pre>';
      h+=`<div class="mut" style="font-size:12px">${esc(r.note)}</div>`;o.innerHTML=h;fi.value=''};
    rd.onerror=()=>{o.innerHTML='<div style="color:#ff8a8a">تعذرت قراءة الملف.</div>'};rd.readAsText(f)};
}
function pnLink(p){return `<div style="margin-top:6px"><button class="btn" data-pn="${esc(p.file)}" dir="ltr" style="padding:6px 10px;font-size:12px">${esc(p.file)}${p.size===0?' (0 بايت)':''}</button><pre class="pn-out" dir="ltr" style="display:none;white-space:pre-wrap;max-height:260px;overflow:auto;margin:4px 0;font-size:11.5px"></pre></div>`}
document.addEventListener('click',async e=>{const b=e.target.closest('[data-pn]');if(!b)return;const o=b.parentElement.querySelector('.pn-out');if(o.style.display==='block'){o.style.display='none';return}
 o.style.display='block';o.textContent='جاري القراءة…';const r=await api('panic/raw',{name:b.dataset.pn});o.textContent=r.ok?(r.size===0?'الملف فاضي (0 بايت) على الجهاز/بعد السحب — لا يوجد محتوى.':r.text):(r.error||'خطأ')});
function bbRender(r){
  const o=$('#bb-res');o.style.display='';const m=r.modem||{};let h='';
  h+=`<h3><span class="l">${esc(r.selected_name||'')}</span></h3>`;
  if(r.verdict){const vc={fault:'#ff8a8a',suspect:'#ffd479',ok:'#35e08a',unknown:'#9ec7ff'}[r.verdict.level]||'#9ec7ff';h+=`<div dir="rtl" style="border:1px solid ${vc};border-radius:10px;padding:10px;margin-bottom:10px"><div class="mut" style="font-size:12px">المشكلة المكتشفة</div><div style="font-weight:700;color:${vc}">${esc(r.verdict.label)}</div></div>`}
  if(m.title)h+=`<div dir="rtl" style="font-weight:600;${m.state==='ok'?'color:var(--ok,#35e08a)':'color:#ff8a8a'}">${esc(m.title)}</div><div class="kvs" style="grid-template-columns:1fr 1fr;margin-top:6px"><div><span>Baseband :</span><span class="v" dir="ltr">${esc(m.baseband)}</span></div><div><span>IMEI :</span><span class="v" dir="ltr">${esc(m.imei)}</span></div></div>`;
  if(r.signals){const n=r.signals.filter(x=>x.on).length;h+=`<div dir="rtl" style="margin-top:10px"><b>مؤشرات الأدلة: ${n} من ${r.signals.length}</b> <span class="mut">(عدد المؤشرات المتحققة، مو نسبة دقة)</span></div><ul dir="rtl" style="list-style:none;margin:4px 0;padding:0">`+r.signals.map(x=>`<li style="${x.on?'':'opacity:.55'}">${x.on?'✔':'✘'} ${esc(x.t)}</li>`).join('')+'</ul>'}
  if(r.findings&&r.findings.length)h+='<div dir="rtl" style="margin-top:10px"><b>الخلاصة (تقديرية):</b></div>'+bbLi(r.findings);
  if(r.poss&&r.poss.length)h+='<div dir="rtl" style="margin-top:10px"><b>الأسباب المحتملة:</b></div>'+bbLi(r.poss);
  if(r.steps&&r.steps.length)h+='<div dir="rtl" style="margin-top:10px"><b>شو نعمل:</b></div>'+bbLi(r.steps);
  if(r.notes){const L=r.notes.split('\n').map(x=>x.trim()).filter(Boolean);h+='<div dir="rtl" style="margin-top:10px"><b>قائمة فحصك لهذا الموديل:</b></div><ul dir="rtl" style="list-style:none;margin:4px 0;padding:0">'+L.map(x=>'<li><label><input type="checkbox"> '+esc(x)+'</label></li>').join('')+'</ul>'}
  const bl=r.baseline;
  if(bl){h+=`<div dir="rtl" style="margin-top:10px"><b>مقارنة مع جهاز سليم محفوظ (${esc(bl.date)}):</b></div>`;
    h+=(bl.missing.length?'<div dir="rtl" style="color:#ff8a8a">مفاتيح مفقودة على هذا الجهاز:</div>'+bbLi(bl.missing):'<div class="ok" dir="rtl">لا توجد مفاتيح مفقودة.</div>');
    if(bl.changed.length)h+='<div dir="rtl" class="mut">قيم مختلفة (قد تكون طبيعية، مثل حالة الـSIM):</div>'+bbLi(bl.changed.map(x=>x.k+': '+x.base+' → '+x.now))}
  else h+='<div dir="rtl" class="mut" style="margin-top:10px">لا يوجد جهاز سليم محفوظ لهذا الموديل للمقارنة. وصّل جهازاً سليماً واضغط «احفظ الجهاز الموصول كجهاز سليم».</div>';
  const bk=r.bb_keys||{};const bks=Object.entries(bk);
  const sl=r.syslog||{};
  h+='<details style="margin-top:12px" dir="rtl"><summary class="mut">تفاصيل الفحص</summary>';
  if(r.log_path)h+=`<div class="mut" style="font-size:12px">حُفظ السجل بملف: <span dir="ltr">${esc(r.log_path)}</span></div>`;
  if(sl.available){h+=`<div>سجل الجهاز: ${sl.total} سطر (${esc(sl.source||'')}) | مرتبط بالمودم: ${sl.kw} | فيه أخطاء: ${sl.bad}${sl.tagged?` | سطور Error عامة: ${sl.tagged}`:''}</div>`;
    const ls=(sl.bad_lines&&sl.bad_lines.length)?sl.bad_lines:(sl.sample||[]);if(ls.length)h+='<pre dir="ltr" style="white-space:pre-wrap;max-height:160px;overflow:auto;font-size:11.5px">'+esc(ls.join('\n'))+'</pre>'}
  else h+='<div style="color:#ff8a8a">'+esc(sl.error||'سجل الجهاز غير متاح')+'</div>';
  const io=r.ioreg||{};if(io.available)h+='<div>IORegistry: '+(io.tokens.length?io.tokens.length+' عنصر مرتبط بالمودم':'لا يوجد عنصر مرتبط بالمودم')+'</div>'+(io.tokens.length?'<pre dir="ltr" style="white-space:pre-wrap;max-height:100px;overflow:auto;font-size:11.5px">'+esc(io.tokens.join('\n'))+'</pre>':'');
  if(r.panics&&r.panics.length)h+='<div>تقارير panic:</div>'+r.panics.map(p=>pnLink(p)).join('');
  if(bks.length)h+='<div style="margin-top:8px">بيانات المودم الخام من الجهاز:</div><pre dir="ltr" style="white-space:pre-wrap;max-height:160px;overflow:auto;font-size:11.5px">'+esc(bks.map(([k,v])=>k+' = '+v).join('\n'))+'</pre>';
  else h+='<div style="margin-top:8px">لم تظهر أي بيانات مودم من الجهاز.</div>';
  h+='</details>';
  o.innerHTML=h;
  loadFlow(r.flow_pt||'',r.verdict&&r.verdict.label);
}
/* ---------- Panic ---------- */
function guideHTML(gl,dev){
  if(!gl||!gl.length)return '';
  return `<div dir="rtl" style="margin-top:10px;border:1px solid var(--cy);border-radius:8px;padding:8px"><b>مرجع — إشارات وليست تشخيصاً نهائياً</b>${dev?`<div dir="ltr" style="margin-top:6px;font-weight:700;color:var(--cy)">${esc(dev)}</div>`:''}${gl.map((g,i)=>`<div style="margin-top:8px">${g.value?` <span dir="ltr" class="bad">${esc(g.value)}${g.hexv?' ('+esc(g.hexv)+')':''}</span>`:''}<div style="margin-top:4px;border:1px solid #3ddc84;background:rgba(61,220,132,.10);border-radius:6px;padding:8px"><div dir="auto" style="color:#ff6b6b;font-weight:700">${esc(g.part)}</div>${g.check?`<div dir="auto" style="margin-top:6px"><b style="color:#3ddc84">الحل / الفحص:</b> ${esc(g.check)}</div>`:''}</div></div>`).join('')}</div>`;
}
function panicHTML(r,title){
  return `${title?`<h4 style="color:var(--cy);margin:8px 0">${esc(title)}</h4>`:''}${r.size!==undefined?`<div class="mut" dir="auto" style="font-size:12px">الحجم: ${r.size} بايت${r.size===0?' — الملف فارغ!':''}${r.path?` — <span dir="ltr">${esc(r.path)}</span>`:''}</div>`:''}${r.device?`<div dir="auto" style="margin:4px 0"><b>الجهاز من السجل:</b> <span dir="ltr">${esc(r.device)}</span></div>`:''}${r.summary?`<div class="mut" dir="ltr" style="font-size:12px;word-break:break-all">${esc(r.summary)}</div>`:''}
  <div class="bars">${r.scores.filter(s=>!s.cat.startsWith('الفلاتات')).map(s=>`<div class="b"><span dir="auto"><b>${esc(s.cat)}</b><b>${s.pct}%</b></span><div class="bar"><i style="width:${s.pct}%"></i></div></div>`).join('')||((r.guide&&r.guide.length)||r.scores.length?'':'<div class="empty">لم تُطابق أي قاعدة معروفة. أرسل السجل لإضافة نمط جديد.</div>')}</div>
  <ul dir="auto">${r.notes.map(n=>`<li>${n.cat.startsWith('الفلاتات')?'':`<b>[${esc(n.cat)}]</b> `}${esc(n.msg)}</li>`).join('')}</ul>${guideHTML(r.guide)}`;
}

/* ---------- سحب panic تلقائي ---------- */
async function autoPoll(){
  const r=await api('auto/status',{});if(!r||r.error||!r.ok)return;
  $('#au-state').textContent=r.state||'';
  let h='';const pg=r.prog||{};
  if(pg.busy)h+=`<div dir="rtl" style="display:flex;align-items:center;gap:14px;border:1px solid var(--cy);border-radius:10px;padding:12px;margin-bottom:10px"><span class="gear">⚙</span><div style="flex:1"><div style="font-size:26px;font-weight:800" dir="ltr">${pg.pct}%</div><div class="bar"><i style="width:${pg.pct}%"></i></div><div class="mut" style="margin-top:4px">${esc(pg.stage||'')}</div></div></div>`;
  if(!pg.busy&&r.results&&r.results.length){
    const gs=[];r.results.forEach(x=>(x.guide||[]).forEach(g=>{if(!gs.some(y=>y.part===g.part&&y.model===g.model))gs.push(g)}));
    if(!pg.busy&&gs.length)h+='<div dir="rtl" style="font-weight:800;margin-bottom:2px">الخلاصة</div>'+guideHTML(gs,(r.results.find(x=>x.device)||{}).device);
    h+=`<details ${gs.length&&!pg.busy?'':'open'} style="margin-top:10px"><summary dir="rtl" style="cursor:pointer">التقارير المحلّلة (${r.results.length})</summary>`+r.results.map(x=>`<div class="mut" dir="auto" style="margin-top:6px">وصل ${esc(x.when)}</div>`+panicHTML(x,x.file)).join('')+'</details><p class="note" dir="auto">استدلال من السجل — معنى الكود يختلف بين الموديلات، والتأكيد بالقياس على البورد.</p>';
  }
  else if(!pg.busy)h+='<div class="mut" dir="auto">لا توجد تقارير panic جديدة. '+(r.present?'الهاتف ظاهر؛ يسحب تلقائياً عند التوصيل وبعد كل ريستارت.':'وصّل الهاتف وسيسحب تلقائياً.')+'</div>';
  if(r.note)h+='<div class="note" dir="auto">'+esc(r.note)+'</div>';
  if(r.pulls&&r.msg&&(r.missing||r.note||!r.results.length))h+='<pre dir="ltr" style="text-align:left;white-space:pre-wrap;font-size:11.5px;max-height:140px;overflow:auto">'+esc(r.msg)+'</pre>';
  if(window._auH===h)return;window._auH=h;
  $('#au-out').innerHTML=h;
}
$('#au-now').onclick=async()=>{$('#au-out').innerHTML='<div class="empty">جاري السحب… (افتح قفل الهاتف إن طلب)</div>';await api('auto/now',{});setTimeout(autoPoll,1500)};
setInterval(autoPoll,1000);autoPoll();

/* ---------- قراءة لوغ المودم المباشرة ---------- */
async function blStatus(){
  const r=await api('bblive/status',{});if(!r||!r.ok)return;
  $('#bl-state').textContent=r.on?'● Reading':'Stopped';
  $('#bl-msg').textContent=r.on?('Modem lines: '+r.total+' ('+r.kinds+' kinds)'+(r.since?' since '+r.since:'')):'';$('#bl-msg').dir='ltr';
}
function blTable(rows){
  return '<table style="width:100%;font-size:12.5px"><tr><th>Count</th><th>First</th><th>Last</th><th dir="ltr" style="text-align:left">Line (as received)</th></tr>'+rows.map(e=>'<tr><td>'+e.n+'</td><td>'+esc(e.first)+'</td><td>'+esc(e.last)+'</td><td dir="ltr" style="text-align:left;word-break:break-all">'+esc(e.sample)+'</td></tr>').join('')+'</table>';
}
let _blRun=0;
function blProg(pct,left){const o=$('#bl-prog');o.style.display='';
if(!o.querySelector('.rg')){o.innerHTML=`<div dir="ltr" class="scn"><div class="rgw"><svg viewBox="0 0 120 120" class="rg"><circle cx="60" cy="60" r="52" class="rgb"/><circle cx="60" cy="60" r="52" class="rgf" id="rgf"/></svg><div class="rgt"><b id="rgp">0%</b><small id="rgl"></small></div><span class="sweep"></span></div><div class="scr"><div class="scs" id="scs"></div><div class="bar scbar"><i id="scbi" style="width:0"></i></div><div class="mut" id="sct" style="margin-top:8px"></div></div></div>`}
const C=2*Math.PI*52;$('#rgf').style.strokeDasharray=(C*pct/100)+' '+C;$('#rgp').textContent=pct+'%';$('#rgl').textContent=left+' s';$('#scbi').style.width=pct+'%';
const st=[['Connect',0],['Collect logs',15],['Analyze',85],['Result',100]];const cur=pct>=85?2:pct>=15?1:0;
$('#scs').innerHTML=st.map((x,k)=>`<span class="scc ${k<cur?'d':k==cur?'a':''}">${k<cur?'✓ ':k==cur?'● ':'○ '}${x[0]}</span>`).join('');
$('#sct').textContent='Reading device log — '+left+' s left'}
function blProgHide(){const o=$('#bl-prog');o.style.display='none';o.innerHTML=''}
function blStop(){clearInterval(window._blT);_blRun=0}
async function blShow(pre){
  const r=pre||await api('bblive/analyze',{});if(!r||!r.ok)return toast('Analysis failed');
  let h='<div dir="ltr"><b>Modem lines read:</b> '+r.total+' ('+r.kinds+' kinds)'+(r.since?' since '+esc(r.since):'')+'</div>';
  const bo=r.boot;let _hw0=0;
  if(bo&&bo.timeline&&bo.timeline.length){
    const bad=bo.to_resetting>0;
    h+='<div dir="ltr" style="margin-top:10px;border-inline-start:3px solid '+(bad?'#ff4d5e':'#2ecc71')+';padding:6px 10px"><b>Baseband boot states (as written in the log)</b>'
      +'<div>Reached <b>IsReady</b>: <b>'+(bo.reached_ready?'yes':'no')+'</b> &nbsp; | &nbsp; Transitions to <b>Resetting</b>: <b>'+bo.to_resetting+'</b> &nbsp; | &nbsp; Reset requests: <b>'+bo.resets+'</b></div>'
      +(bo.stat&&(bo.stat.boot_ms||bo.stat.push_ms)?'<div class="mut">Boot time (from log): '+(bo.stat.boot_ms||'?')+' ms &nbsp; Image push: '+(bo.stat.push_ms||'?')+' ms</div>':'')
      +(bo.ref?'<div style="margin-top:4px"><b>Healthy reference ('+esc(bo.ref.name)+'):</b> reached IsReady = yes, Resetting = '+bo.ref.resets+', boot '+bo.ref.boot_ms+' ms, image push '+bo.ref.push_ms+' ms <span class="mut">('+esc(bo.ref.src)+')</span></div>':'<div class="mut" style="margin-top:4px">No healthy boot reference stored for this model.</div>')
      +(bo.reasons.length?'<div style="margin-top:4px">Reset reason lines:</div>'+bo.reasons.map(x=>'<div style="color:#ff4d5e;font-size:12.5px">'+x.n+' × '+esc(x.text)+'</div>').join(''):'')
      +'<table class="tlt" dir="ltr"><tr><th style="text-align:left">Time</th><th>Event</th><th>Detail</th></tr>'
      +bo.timeline.map(e=>'<tr><td style="text-align:left">'+esc(e.t)+'</td><td>'+(e.k==='state'?esc(e.a)+' → '+esc(e.b):e.k==='reset'?'Reset requested':'PCIe')+'</td><td style="white-space:normal">'+(e.k==='state'?'':e.k==='reset'?esc(e.a)+' — '+esc(e.b):esc(e.b))+'</td></tr>').join('')
      +'</table><div class="mut" style="font-size:12px">Text taken from the log only. The log gives no reason beyond the text shown. No part is named.</div></div>';
  }
  if(bo&&bo.stage&&bo.stage.stop!==null&&bo.stage.stop!==undefined&&!bo.partial){const S=bo.stage.stages,x=bo.stage.stop;
    const row=(n,k)=>'<div style="display:flex;justify-content:space-between;align-items:center;padding:5px 10px;margin-top:4px;border-radius:6px;background:'+(k==='bad'?'rgba(255,77,94,.14);border:1px solid #ff6b6b':'rgba(255,255,255,.03)')+'"><span style="color:#9fd0ff;font-weight:700">'+esc(n)+'</span>'+(k==='ok'?'<span style="color:#2ecc71;font-weight:700">✔ OK</span>':'<span style="color:#ff6b6b;font-weight:700">✖ Stopped here</span>')+'</div>';
    const N=x===-1?S.length:x>=0?x+1:0;
    h+='<div dir="ltr" style="margin-top:10px;padding:8px 10px;border:1px solid rgba(255,255,255,.08);border-radius:8px;text-align:left"><div style="font-weight:700;margin-bottom:4px">Baseband check (boot order)</div>'
      +S.slice(0,N).map((n,i)=>row(n,i===x?'bad':'ok')).join('')
      +(x===-2?'<div style="margin-top:6px;color:#ff6b6b;font-size:13px">Baseband did not reach Ready; the scan does not show which part.</div>':'')
      +(x>=0?'<div dir="rtl" style="margin-top:6px;font-size:13px;text-align:right">⬇ شوف صندوق الحل تحت: بيبلّش من هون.</div>':'')+'</div>'}
  if(bo&&bo.partial)h+='<div dir="rtl" style="margin-top:10px;border-inline-start:3px solid #f5b942;padding:6px 10px;background:rgba(245,185,66,.08)"><b style="color:#f5b942">سكان ناقص</b><div style="font-size:13px">البيسباند لا يعمل، لكن لحظة الإقلاع ما انمسكت (لا سبب ريست باللوغ). النتيجة مش أكيدة: أعد السكان مع Restart.</div></div>';
  _hw0=h.length;
  if(bo&&bo.hw&&bo.hw.length){
    const hwItem=x=>'<div dir="rtl" style="margin-top:6px;padding:5px 8px;border-inline-start:2px solid rgba(255,255,255,.18);background:rgba(255,255,255,.03);text-align:right"><a href="#" dir="ltr" data-hwnet="'+esc(x.net)+'" style="font-size:14px;font-weight:700;color:#ffd479;text-decoration:underline;cursor:pointer;unicode-bidi:isolate">'+esc(x.net)+'</a> <span class="mut" dir="ltr" style="font-size:12px;unicode-bidi:isolate">('+esc(x.model)+')</span>'+(x.when?'<div dir="rtl" style="margin-top:3px;font-size:13px;color:#c8d2e0">إذا: '+esc(x.when)+'</div>':'')+(x.ldo&&x.ldo.length?'<div dir="rtl" style="margin-top:3px;font-size:12px;color:#9fe3b5">وافحص الممانعة والفولط على: <span dir="ltr">'+x.ldo.map(esc).join(' · ')+'</span></div>':'')+(x.fix?'<div dir="rtl" style="margin-top:3px;font-size:13.5px;color:#8fd3ff">'+esc(x.fix)+'</div>':'')+'</div>';
    const s2Item=x=>'<div dir="rtl" style="margin-top:6px;padding:6px 10px;border-inline-start:2px solid #ffd479;background:rgba(255,255,255,.03);text-align:right"><div style="font-size:14px;color:#5aa9ff;font-weight:700">إذا لقيت فولت على: '+esc(x.when)+'</div><div style="margin-top:3px">← افحص: <a href="#" dir="ltr" data-hwnet="'+esc(x.net)+'" style="font-size:14px;font-weight:700;color:#ffd479;text-decoration:underline;cursor:pointer;unicode-bidi:isolate">'+esc(x.net)+'</a></div>'+(x.ldo&&x.ldo.length?'<div style="margin-top:3px;font-size:12px;color:#9fe3b5">وافحص الممانعة والفولط على: <span dir="ltr">'+x.ldo.map(esc).join(' · ')+'</span></div>':'')+(x.fix?'<div style="margin-top:3px;font-size:13.5px;color:#8fd3ff">'+esc(x.fix)+'</div>':'')+'</div>';
    const s2Html=A=>{let o='',i=0;while(i<A.length){const w=A[i].when;let j=i;while(j<A.length&&A[j].when===w)j++;const G=A.slice(i,j);
      o+='<div dir="rtl" style="margin-top:6px;padding:6px 10px;border-inline-start:2px solid #ffd479;background:rgba(255,255,255,.03);text-align:right"><div style="font-size:14px;color:#5aa9ff;font-weight:700">إذا لقيت فولت على: '+esc(w)+'</div>'
        +G.map(x=>(x.fix?'<div style="margin-top:3px;font-size:13.5px;color:#8fd3ff">'+esc(x.fix)+'</div>':'')+'<div style="margin-top:3px">← افحص: <a href="#" dir="ltr" data-hwnet="'+esc(x.net)+'" style="font-size:14px;font-weight:700;color:#ffd479;text-decoration:underline;cursor:pointer;unicode-bidi:isolate">'+esc(x.net)+'</a></div>'+(x.ldo&&x.ldo.length?'<div style="margin-top:3px;font-size:12px;color:#9fe3b5">وافحص الممانعة والفولط على: <span dir="ltr">'+x.ldo.map(esc).join(' · ')+'</span></div>':'')).join('')+'</div>';i=j}return o};
    const grp=arr=>{let o='';arr.forEach((x,i)=>{const nx=arr[i+1];const same=nx&&nx.fix&&nx.fix===x.fix;o+=hwItem(Object.assign({},x,{fix:same?'':x.fix}))});return o};
    const sub2=t=>'<div dir="rtl" style="margin-top:8px;font-weight:700;color:#9fd0ff;font-size:13.5px">'+t+'</div>';
    const s3Html=S=>{const G=[['pwr','أ) البور وساعة المودم:'],['rst','ب) فلت ريست البيسباند:'],['spmi','ج) الحكي بين البيسباند والبور (SPMI) وقراءة مقاومات الـ ID:'],['pcie','د) الربط مع المعالج (PCIe):']],ks=G.map(x=>x[0]),O=S.filter(x=>ks.indexOf(x.sub)<0);
      return G.map(([k,t])=>{const a=S.filter(x=>x.sub===k);return a.length?sub2(t)+grp(a):''}).join('')+(O.length?sub2('هـ) البقية:')+grp(O):'')};
    const mtAr=t=>{t=t||'Voltage + Impedance';const c=t.match(/^Impedance \+ Coil (\S+)$/);if(c)return 'ممانعة والملف '+c[1];return ({'Voltage + Impedance + Signal':'ممانعة مع فولط وإشارة','Voltage + Impedance':'ممانعة مع فولط','Impedance + Signal':'ممانعة مع إشارة','Impedance':'ممانعة','Signal':'إشارة','Resistance':'قيمة المقاومة'})[t]||t};
    const cpN=s=>String(s).split(' · ').map(n=>'<span class="cpn" data-cp="'+esc(n)+'" title="نسخ" style="cursor:pointer;text-decoration:underline dotted">'+esc(n)+'</span>').join(' · ');
    const flowHtml=A=>{const seqs=[...new Set(A.map(x=>x.seq))].sort((a,b)=>a-b),wiz=true,fid='fw'+Math.random().toString(36).slice(2,8);let o='';
      const endTxt=(A.find(x=>x.flowend)||{}).flowend||'كل الخطوط بهالصندوق سليمة.';
      seqs.forEach((n,i)=>{const G=A.filter(x=>x.seq===n).sort((a,b)=>(a.ord||0)-(b.ord||0));const last=i===seqs.length-1;
        const m=String(G[0].flow).match(/^(.*?) \((.*)\)$/),nt=m?m[1]:G[0].flow,ds=m?m[2]:'';
        const bad=G.map((x,j)=>{const f=(x.fixbad!==null&&x.fixbad!==undefined)?x.fixbad:x.fix;const nx=G[j+1],nf=nx?((nx.fixbad!==null&&nx.fixbad!==undefined)?nx.fixbad:nx.fix):null;return f&&f!==nf?'<div style="margin:3px 14px 0 0;font-size:13.5px;color:#8fd3ff">'+esc(f)+'</div>':''}).join('');
        const BR=(G.find(x=>x.branch&&x.branch.length)||{}).branch,gone=(G.find(x=>x.gone)||{}).gone,bend=(G.find(x=>x.branchend)||{}).branchend;
        const goneH=gone?'<div style="margin:4px 14px 0 0;font-size:12.5px;color:#8a96a8">ⓘ '+esc(gone)+'</div>':'';
        const subH=BR?BR.map((b,k)=>'<div class="fsub" data-k="'+k+'" style="margin:6px 14px 0 0;padding:4px 8px;border-inline-start:2px solid #ffd479;'+(wiz&&k>0?'display:none':'')+'"><div style="font-size:13px;color:#5aa9ff">'+(BR.length>1?'('+(k+1)+') ':'')+'افحص '+esc(mtAr(b[1]))+': <span dir="ltr" style="color:#ffd479;font-weight:700;unicode-bidi:isolate">'+cpN(b[0])+'</span></div>'
            +(wiz?'<div class="sbtns" style="margin-top:4px;display:flex;gap:6px"><button class="sok" style="background:rgba(46,204,113,.15);color:#2ecc71;border:1px solid #2ecc71;border-radius:6px;padding:2px 10px;cursor:pointer">✔ OK</button><button class="sbad" style="background:rgba(255,77,94,.12);color:#ff6b6b;border:1px solid #ff6b6b;border-radius:6px;padding:2px 10px;cursor:pointer">✖ NOT OK</button></div><div class="sres" style="display:none;color:#2ecc71;font-size:13px">✔ OK</div><div class="sbadt" style="display:none;font-size:13.5px;color:#ff6b6b;font-weight:700">✖ NOT OK</div>'
                 :'')+'</div>').join('')
            +(bend?'<div class="fsubend" style="margin:6px 14px 0 0;font-size:13.5px;color:#8fd3ff;'+(wiz?'display:none':'')+'">'+(wiz?'':'إذا كلهم سليمين: ')+esc(bend)+'</div>':''):'';
        const badAll=BR?subH:bad;
        const DI=(G.find(x=>x.dio&&x.dio.length)||{}).dio,dioH=DI?'<div style="margin:4px 14px 0 0;font-size:12.5px;color:#c8d2e0">قيمة الدايود الطبيعية على الإنتربوزر:'+DI.map(d=>'<div dir="rtl" style="text-align:right;color:#9fe3b5">'+(d[0]?'<span dir="ltr" style="unicode-bidi:isolate">'+esc(d[0])+'</span> ← ':'')+'بيسباند: <span dir="ltr" style="unicode-bidi:isolate">'+esc(d[1])+'</span>  ·  معالج: <span dir="ltr" style="unicode-bidi:isolate">'+esc(d[2])+'</span></div>').join('')+'</div>':'';
        const LY=(G.find(x=>x.layer&&x.layer.length)||{}).layer,layH=LY?'<div style="margin:4px 14px 0 0;font-size:12.5px;color:#c8d2e0">اسم الخط على طبقة المعالج:'+LY.map(l=>'<div dir="ltr" style="text-align:right"><span style="color:#ffd479;font-weight:700">'+cpN(l[1])+'</span></div>').join('')+'</div>':'';
        o+='<div class="fstep" data-f="'+fid+'" data-i="'+i+'" dir="rtl" style="margin-top:8px;padding:6px 10px;border-inline-start:3px solid #5aa9ff;background:rgba(255,255,255,.03);text-align:right;'+(wiz&&i>0?'display:none':'')+'">'
          +'<div style="font-weight:700;color:#5aa9ff">الخطوة '+(i+1)+(wiz?' من '+seqs.length:'')+': افحص '+esc(mtAr(G[0].mtype))+': <span dir="ltr" style="color:#ffd479;unicode-bidi:isolate">'+cpN(nt)+'</span></div>'
          +(wiz?'<div class="fbtns" style="margin-top:6px;display:flex;gap:8px"><button class="fok" data-f="'+fid+'" data-i="'+i+'" style="background:rgba(46,204,113,.15);color:#2ecc71;border:1px solid #2ecc71;border-radius:6px;padding:4px 12px;cursor:pointer">✔ OK</button><button class="fbad" data-f="'+fid+'" data-i="'+i+'" style="background:rgba(255,77,94,.12);color:#ff6b6b;border:1px solid #ff6b6b;border-radius:6px;padding:4px 12px;cursor:pointer">✖ NOT OK</button></div>'
               +'<div class="fres" style="display:none;margin-top:4px"></div><div class="fbadbox" style="display:none">'+'<div style="margin-top:4px;color:#ff6b6b;font-weight:700">'+(BR?'✖ NOT OK → Check:':'✖ NOT OK')+'</div>'+badAll+layH+dioH+'</div>'
             :'<div style="margin-top:4px;color:#2ecc71">✔ إذا الفولط والممانعة سليمين: '+(last?esc(endTxt):'روح للخطوة '+(i+2)+'.')+'</div><div style="margin-top:4px;color:#ff6b6b">✖ إذا لأ:</div>'+badAll)
          +'</div>'});
      if(wiz)o+='<div class="fend" data-f="'+fid+'" dir="rtl" style="display:none;margin-top:8px;padding:6px 10px;border-inline-start:3px solid #2ecc71;color:#2ecc71;text-align:right">✔ '+esc(endTxt)+'</div><div dir="rtl" style="margin-top:6px;text-align:right"><button class="freset" data-f="'+fid+'" style="background:none;color:#9fd0ff;border:1px solid #9fd0ff;border-radius:6px;padding:3px 10px;cursor:pointer">↺ Start Over</button></div>';
      if(wiz&&!window._fwInit){window._fwInit=1;document.addEventListener('click',e=>{const cp=e.target.closest('.cpn');if(cp){e.preventDefault();const t=cp.dataset.cp,done=()=>toast('انتسخ: '+t);try{navigator.clipboard.writeText(t).then(done,()=>{fb()})}catch(_){fb()}function fb(){const a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();try{document.execCommand('copy')}catch(_){}a.remove();done()}return}const sb=e.target.closest('.sok,.sbad');if(sb){e.preventDefault();const sub=sb.closest('.fsub'),box=sub.parentElement,subs=[...box.querySelectorAll('.fsub')],k=subs.indexOf(sub),se=box.querySelector('.fsubend');sub.querySelector('.sbtns').style.display='none';
          if(sb.classList.contains('sok')){sub.querySelector('.sres').style.display='';if(subs[k+1])subs[k+1].style.display='';else if(se)se.style.display=''}else{sub.querySelector('.sbadt').style.display='';subs.slice(k+1).forEach(x=>x.style.display='none');if(se)se.style.display='none'}return}
        const b=e.target.closest('.fok,.fbad,.freset');if(!b)return;e.preventDefault();const f=b.dataset.f,all=[...document.querySelectorAll('.fstep[data-f="'+f+'"]')],end=document.querySelector('.fend[data-f="'+f+'"]');
        if(b.classList.contains('freset')){all.forEach((s,k)=>{s.style.display=k?'none':'';s.querySelector('.fbtns').style.display='flex';s.querySelector('.fres').style.display='none';s.querySelector('.fbadbox').style.display='none';s.querySelectorAll('.fsub').forEach((x,q)=>{x.style.display=q?'none':'';x.querySelector('.sbtns').style.display='flex';x.querySelector('.sres').style.display='none';x.querySelector('.sbadt').style.display='none'});const se=s.querySelector('.fsubend');if(se)se.style.display='none'});if(end)end.style.display='none';return}
        const i=+b.dataset.i,s=all[i];s.querySelector('.fbtns').style.display='none';
        if(b.classList.contains('fok')){const r=s.querySelector('.fres');r.style.display='';r.innerHTML='<span style="color:#2ecc71">✔ OK</span>';if(all[i+1])all[i+1].style.display='';else if(end)end.style.display=''}
        else{s.querySelector('.fbadbox').style.display='';all.slice(i+1).forEach(x=>x.style.display='none');if(end)end.style.display='none'}})}
      return o};
    const FL0=bo.hw.filter(x=>x.seq),FL=FL0.length?FL0:bo.hw.map((x,i)=>Object.assign({},x,{seq:i+1,flow:x.flow||x.net})),NF=FL0.length?bo.hw.filter(x=>!x.seq):[];
    const st=n=>bo.hw.filter(x=>x.step===n).sort((a,b)=>(a.ord||0)-(b.ord||0)),plain=bo.hw.filter(x=>!x.step);
    const S1=st(1),S2=st(2),S3=st(3);
    const hdr=t=>'<div dir="rtl" style="margin-top:10px;font-weight:700;color:#5aa9ff">'+t+'</div>';
    h+='<div dir="rtl" style="margin-top:10px;border-inline-start:3px solid #2ecc71;padding:8px 12px;background:rgba(46,204,113,.08)">'
      
      +(FL.length?hdr('افحص بالترتيب، ووقف عند أول خطوة فيها مشكلة:')+flowHtml(FL)+(NF.length?hdr('وأيضاً:')+NF.map(hwItem).join(''):''):S1.length||S2.length||S3.length?
         (S1.length?hdr('الخطوة 1: قيس إشارة الريست بين البور الرئيسي وبور البيسباند (GPIO_SEQ_PMU_TO_BBPMU_RESET_L). إذا ما عليها فولت:')+S1.map(hwItem).join(''):'')
        +(S2.length?hdr((S1.length?'الخطوة 2: إذا عليها فولت، قيس خطوط SMPS (SMPS1_6، SMPS2، SMPS3، SMPS4، SMPS5، SMPS7) وشوف على أي منها في فولت:':'قيس خطوط SMPS وشوف على أي منها في فولت:'))+s2Html(S2):'')
        +(S3.length?hdr((S2.length?'الخطوة 3: إذا كل خطوط SMPS عليها فولت، افحص بالترتيب:':'افحص بالترتيب:'))+s3Html(S3):'')
        +(plain.length?hdr('وأيضاً:')+plain.map(hwItem).join(''):'')
       :plain.map(hwItem).join(''))
      +'</div>';
  }
  const hwH=h.slice(_hw0);
  if(!r.total)h+='<div class="note" dir="ltr">No modem-related line was captured during the scan. If the phone has no modem firmware, no lines may appear; that is information, but not a diagnosis.</div>';
  if(r.rules&&r.rules.length)h+='<div dir="ltr" style="margin-top:8px"><b>Matching documented rules:</b></div><table style="width:100%;font-size:12.5px"><tr><th>Count</th><th>Rule</th><th>Meaning</th><th>Source</th></tr>'+r.rules.map(x=>'<tr><td>'+x.count+'</td><td dir="ltr">'+esc(x.pattern)+'</td><td>'+esc(x.meaning)+'</td><td>'+esc(x.source)+'</td></tr>').join('')+'</table>';
  else h+='<div class="note" dir="ltr" style="margin-top:8px">No matching rule'+(r.rules_count?'':' (rules file is empty)')+'. Lines are explained only by rules you add with their source: <span>'+esc(r.rules_file)+'</span></div>';
  if(r.rows&&r.rows.length)h+='<div dir="ltr" style="margin-top:8px"><b>Evidence (most frequent first):</b></div>'+blTable(r.rows);
  const b=r.baseline;
  if(b){
    if(!b.available)h+='<div class="note" dir="ltr" style="margin-top:10px">No healthy-phone baseline stored for this model'+(b.model?' ('+esc(b.model)+')':'')+'. To add one, put a healthy scan folder (syslog_...) in: <span>'+esc(b.refs_dir||'')+'</span>\\&lt;model folder&gt;</div>';
    else{
      h+='<div dir="ltr" style="margin-top:12px"><b>Compared with healthy baseline:</b> '+esc(b.name)+' — healthy modem line kinds seen now: '+b.seen_of_base+' / '+b.base_modem_kinds+'</div>';
      h+='<div class="note" dir="ltr">For a fair comparison press Restart Phone during the scan (the baseline includes the boot). Numbers inside lines are shown as #.</div>';
      h+='<div dir="ltr" style="margin-top:8px"><b>Modem lines the healthy phone shows that did not appear now ('+b.missing_total+' kinds):</b></div>';
      h+=b.missing.length?blTable(b.missing.map(e=>({n:e.n,first:'',last:'',sample:e.line}))):'<div class="mut" dir="ltr">None.</div>';
      h+='<div dir="ltr" style="margin-top:8px"><b>Error/Fault lines not seen on the healthy phone ('+b.new_err_total+' kinds):</b></div>';
      h+=b.new_err.length?blTable(b.new_err.map(e=>({n:e.n,first:'',last:'',sample:e.line}))):'<div class="mut" dir="ltr">None.</div>';
    }
  }
  h+='<p class="note" dir="ltr">Raw log lines, no verdict. Confirm by measuring on the board.</p>';
  h+='<div id="bl-bbf"></div>';
  if(!OWNER)h=hwH||'<div class="note" dir="rtl" style="font-size:15px;line-height:1.9">لم يُكتشف أي عطل هاردوير في هذا السكان. الهاتف غالباً يحتاج إلى معالجة سوفت وير فقط: حدّث نظام iOS أو أعد تثبيته (Update / Restore)، ثم أعد السكان للتأكد.</div>';
  const _dn=($('#d-name')&&$('#d-name').textContent||'').trim().replace(/^لا يوجد جهاز$/,'');
  const _lg='<svg viewBox="0 0 128 128" fill="none" width="40" height="40"><rect x="30" y="8" width="68" height="112" rx="16" stroke="#35d0ff" stroke-width="6"/><path d="M50 94V48L78 94V48" stroke="#2f6bff" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  h='<div class="rsh">'+_lg+'<div><b>نتيجة السكان</b>'+(_dn?'<small dir="auto">'+esc(_dn)+'</small>':'')+'</div></div><div class="rsb">'+h+'</div>';
  $('#bl-out').innerHTML=h;blBbf(0)}
async function blBbf(k){
  const r=await api('bblive/bbfiles',{});const o=$('#bl-bbf');if(!r||!o)return;
  if(r.state==='run'&&k<60)return setTimeout(()=>blBbf(k+1),3000);
  if(r.state==='done'&&r.n>0)o.innerHTML='<div dir="ltr" style="margin-top:10px;border-inline-start:3px solid #f5a623;padding:6px 10px"><b>Baseband files found on the phone ('+r.n+')</b>'+r.files.map(x=>'<div style="font-size:12.5px">'+esc(x.t)+' &nbsp; '+esc(x.f)+'</div>').join('')+'<div class="mut" style="font-size:12px">Saved in: '+esc(r.dest)+'. Files only; not interpreted.</div></div>'}
async function blRunScan(attempt){
  const SECS=100;_blRun=1;window._blTick=0;
  await api('syslog/stop',{});await new Promise(r=>setTimeout(r,800));
  await api('syslog/start',{kernel_only:false});
  if(attempt>0){await new Promise(r=>setTimeout(r,1500));const rr=await api('device/restart',{});
    $('#bl-msg').textContent=rr&&rr.ok?'لحظة الإقلاع ما انمسكت بالسكان اللي قبل: إعادة تلقائية مع Restart (محاولة '+attempt+' من 2).':'تعذّر إرسال Restart تلقائياً: اكبس Restart Phone بإيدك.'}
  const t0=Date.now();
  $('#bl-scan').disabled=true;
  window._blT=setInterval(async()=>{
    if(window._blTick)return;window._blTick=1;try{
    const el=Math.floor((Date.now()-t0)/1000),left=Math.max(0,SECS-el);
    $('#bl-cnt').textContent=(attempt>0?'('+attempt+'/2) ':'')+left+' s';blProg(Math.min(99,Math.floor(el*100/SECS)),left);
    await blStatus();
    if(left<=0){blStop();
      const stg=async(n,p)=>{const t1=Date.now();$('#bl-msg').textContent='● '+n+'…';$('#bl-msg').dir='ltr';
        const w=setTimeout(()=>{$('#bl-msg').textContent='⚠ Still waiting: '+n+' (>15 s)'},15000);
        try{return await p()}finally{clearTimeout(w);window._blLog=(window._blLog||[]).concat(n+' '+(Date.now()-t1)+' ms')}};
      await stg('Stopping log',()=>api('syslog/stop',{}));$('#bl-cnt').textContent='Done';blProgHide();await blStatus();
      const r=await stg('Analyzing',()=>api('bblive/analyze',{}));
      if(r&&r.ok&&r.boot&&r.boot.partial&&attempt<2){toast('لحظة الإقلاع ما انمسكت: عم أعيد السكان تلقائياً');return blRunScan(attempt+1)}
      $('#bl-scan').disabled=false;await stg('Rendering',()=>blShow(r));$('#bl-msg').textContent='End steps: '+(window._blLog||[]).slice(-3).join(' | ');window._blLog=[]}
    }catch(e){blStop();$('#bl-scan').disabled=false;blProgHide();$('#bl-msg').textContent='⚠ Error during scan: '+(e&&e.message||e)}finally{window._blTick=0}
  },1000)}
$('#bl-scan').onclick=async()=>{if(_blRun)return;$('#bl-out').innerHTML='';$('#bl-msg').textContent='';blRunScan(0).catch(e=>{blStop();$('#bl-scan').disabled=false;blProgHide();$('#bl-msg').textContent='⚠ Scan could not start: '+(e&&e.message||e)})};
$('#bl-rst').onclick=async()=>{const r=await api('device/restart',{});toast(r.msg||(r.ok?'Restart sent':'Failed'));if(r.ok)$('#bl-msg').textContent='Restart command sent. The scan keeps reading and reconnects when the phone returns.'};
$('#bl-stop').onclick=async()=>{blStop();await api('syslog/stop',{});$('#bl-scan').disabled=false;$('#bl-cnt').textContent='';blProgHide();blStatus()};

/* ---------- مكتبة الأعطال ---------- */
let FAULTS=null;let HWNET='';
let LIBCODE='';const OWNER=false;
document.documentElement.classList.toggle('simple',!OWNER);
async function loadFaults(){if(!FAULTS){const r=await api('faults',{code:LIBCODE});if(r.locked||!r.hw){LIBCODE='';toast('الرمز غير صحيح');show('baseband');return}FAULTS=r}drawFaults()}

function drawFaults(){
  const q=($('#f-q').value||'').toLowerCase();
  const hit=s=>!q||s.toLowerCase().includes(q);
  $('#f-codes').innerHTML=FAULTS.codes.filter(c=>hit(c.code+' '+c.cause+' '+c.cat_ar)).map(c=>`<div class="it" style="display:block"><div style="display:flex;justify-content:space-between"><b class="bad" dir="ltr">${c.code}</b><span class="pill">${esc(c.cat_ar)}</span></div><div dir="auto">${esc(c.cause)}</div></div>`).join('')||'<div class="empty">لا نتائج</div>';
  $('#f-panic').innerHTML=FAULTS.panic.filter(c=>hit(c.msg+' '+c.cat+' '+c.rx)).map(c=>`<div class="it" style="display:block"><div style="display:flex;justify-content:space-between"><span class="mut" dir="ltr" style="font-size:12px">${esc(c.rx.slice(0,48))}</span><span class="pill">${esc(c.cat)}</span></div><div dir="auto">${esc(c.msg)}</div></div>`).join('')||'<div class="empty">لا نتائج</div>';
  $('#f-hw').innerHTML=(FAULTS.hw||[]).filter(c=>HWNET?c.net===HWNET:hit(c.model+' '+c.title+' '+c.symptoms+' '+c.checks+' '+c.result)).map(c=>`<div class="it" style="display:block" dir="auto"><div style="display:flex;justify-content:space-between;gap:8px"><b>${esc(c.title)}</b><span class="pill" dir="ltr">${esc(c.model)}</span></div><div class="mut" style="margin-top:4px">الأعراض: ${esc(c.symptoms)}</div><div style="margin-top:4px">القياسات: ${esc(c.checks)}</div><div style="margin-top:4px;color:#ffd479"><b>النتيجة:</b> ${esc(c.result)}</div><div class="mut" style="margin-top:4px;font-size:12px">${esc(c.verified)}</div>${c.meas?`<div dir="rtl" style="margin-top:8px;padding:6px 10px;border-inline-start:3px solid #5aa9ff;background:rgba(90,169,255,.08)"><b>قياس الفولت مع الشورت على ${esc(c.meas.short)} (${esc(c.meas.date)}):</b><div style="margin-top:4px"><span style="color:#2ecc71">✔ عليه فولت:</span> <span dir="auto">${c.meas.on.length?c.meas.on.map(esc).join('، '):'لا شيء'}</span></div><div><span style="color:#ff6b6b">✖ بدون فولت:</span> <span dir="auto">${c.meas.off.length?c.meas.off.map(esc).join('، '):'لا شيء'}</span></div>${c.meas.note?`<div class="mut" style="font-size:12px">${esc(c.meas.note)}</div>`:''}</div>`:''}${c.ldo&&c.ldo.length?`<div dir="rtl" style="margin-top:8px;padding:6px 10px;border-inline-start:3px solid #2ecc71;background:rgba(46,204,113,.08)"><b>LDO المتغذية من هذه السكة (افحص الممانعة والفولط):</b> <span dir="ltr">${c.ldo.map(esc).join(' · ')}</span></div>`:''}${c.point?`<div dir="ltr" style="margin-top:8px"><b>${esc(c.point)}</b> <span class="mut" style="font-size:12px">(نقطة القياس على iPhone 13 Pro)</span></div>`:''}${c.img?`<img src="${c.img}" alt="" style="margin-top:8px;max-width:100%;border-radius:6px;cursor:zoom-in" onclick="window.open(this.src)">`:''}</div>`).join('')||'<div class="empty">لا نتائج</div>';
  $('#f-sens').innerHTML=(FAULTS.sensors||[]).map(g=>{const rows=g.items.filter(i=>hit(g.group+' '+i.val+' '+i.part+' '+i.note));if(!rows.length)return '';return `<div class="it" style="display:block"><b dir="auto">${esc(g.group)}</b>`+rows.map(i=>`<div style="display:flex;gap:10px;margin-top:4px"><span class="bad" dir="ltr" style="min-width:76px">${esc(i.val)}</span><span dir="auto">${esc(i.part)}${i.note?` <span class="mut">— ${esc(i.note)}</span>`:''}</span></div>`).join('')+'</div>'}).join('')+(FAULTS.legacy&&hit('mic1 mic2 prs0 tg0b ans2 legacy')?`<div class="it" style="display:block"><b>iPhone 12 وما قبل (missing sensor)</b>`+FAULTS.legacy.map(i=>`<div style="display:flex;gap:10px;margin-top:4px"><span class="bad" dir="ltr" style="min-width:76px">${esc(i.name)}</span><span dir="auto">${esc(i.part)}</span></div>`).join('')+'</div>':'')||'<div class="empty">لا نتائج</div>';
}
$('#f-q').oninput=()=>{HWNET='';FAULTS&&drawFaults()};

/* ---------- المخططات ---------- */
let SCH=[];
async function loadSchem(){const r=await api('schem/list');SCH=r.files||[];$('#sc-dir').textContent=r.ok?SCH.length+' ملف':'';if(!r.ok)$('#sc-list').innerHTML=`<div class="empty" dir="auto">${esc(r.error)}</div>`;else drawSchem()}
function drawSchem(){const q=($('#sc-q').value||'').toLowerCase();const L=SCH.filter(p=>p.toLowerCase().includes(q)).slice(0,300);
  $('#sc-list').innerHTML=L.map((p,i)=>`<div class="it"><span dir="ltr" style="word-break:break-all">${esc(p)}</span><button class="btn ghost" data-open="${i}">فتح</button></div>`).join('')||'<div class="empty">لا نتائج</div>';window._sl=L}
$('#sc-q').oninput=drawSchem;$('#sc-ref').onclick=loadSchem;
document.addEventListener('click',e=>{const a=e.target.closest('[data-hwnet]');if(!a||!LIBCODE)return;e.preventDefault();HWNET=a.dataset.hwnet;$('#f-q').value='';show('faults');setTimeout(()=>{const h=$('#f-hw');if(h)h.scrollIntoView({block:'start'})},60)});
document.addEventListener('click',async e=>{const b=e.target.closest('[data-open]');if(b){const r=await api('schem/open',{path:window._sl[+b.dataset.open]});if(!r.ok)toast('تعذر فتح الملف')}});

/* ---------- الأدوات: اكتشاف السجل ---------- */
let DI=[];
$('#m-find').onclick=()=>show('tools');$('#t-back').onclick=()=>show('monitor');
$('#t-start').onclick=async()=>{$('#t-msg').textContent='جاري أخذ اللقطة الأولى… قد تستغرق دقيقة.';const r=await api('disc/start',{});$('#t-msg').textContent=r.ok?`تمت (${r.count} ملف). الآن ابدأ الفلاش في 3uTools.`:r.error};
$('#t-stop').onclick=async()=>{$('#t-msg').textContent='جاري المقارنة…';const r=await api('disc/stop',{});if(!r.ok){$('#t-msg').textContent=r.error;return}
  DI=r.items;$('#t-msg').textContent=`${DI.length} ملف تغيّر.`;
  $('#t-list').innerHTML=DI.map((x,i)=>`<div class="it"><span dir="ltr" style="word-break:break-all;font-size:12px">${esc(x.path)}<br><span class="mut">${x.kind} · ${x.delta} B · ${x.text?'نصي':'غير نصي'}</span></span><span style="display:flex;gap:6px"><button class="btn ghost" data-pv="${i}">معاينة</button><button class="btn ghost" data-ad="${i}">اعتمد مجلده</button></span></div>`).join('')||'<div class="empty">لم يتغير شيء.</div>'};
document.addEventListener('click',async e=>{
  const p=e.target.closest('[data-pv]');if(p){const r=await api('disc/preview',{path:DI[+p.dataset.pv].path});$('#t-prev').value=r.ok?r.text:r.error}
  const a=e.target.closest('[data-ad]');if(a){const path=DI[+a.dataset.ad].path;const dir=path.replace(/[\\/][^\\/]*$/,'');await api('settings',{log_dir:dir});toast('تم اعتماد المجلد');$('#m-folder').value=dir;show('monitor')}
});

async function showDirs(){const s=await api('settings');$('#t-dirs').textContent=(s.scan_dirs||[]).length?'مجلدات إضافية: '+s.scan_dirs.join(' ; '):''}
$('#t-add').onclick=async()=>{const r=await api('settings',{add_scan_dir:$('#t-extra').value});if(r.ok===false){toast(r.error)}else{toast('تمت الإضافة — أعد اللقطة الأولى');$('#t-extra').value='';showDirs()}};
/* ---------- الإعدادات ---------- */
async function loadSettings(){const s=await api('settings');$('#s-log').value=s.log_dir;$('#s-sch').value=s.schem_dir;$('#h-db').textContent=s.db_count;
  $('#s-sug').innerHTML=(s.suggest||[]).map(x=>`<span class="pill" dir="ltr" data-sug="${esc(x)}">${esc(x)}</span>`).join('')||'<span class="mut note">لم يُعثر على مجلد تلقائياً — استخدم تبويب الأدوات لاكتشافه.</span>'}
document.addEventListener('click',e=>{const s=e.target.closest('[data-sug]');if(s)$('#s-log').value=s.dataset.sug});
$('#s-usb').onclick=async()=>{const o=$('#s-usbout');o.style.display='block';o.value='...';const r=await api('usbdebug');o.value=JSON.stringify(r,null,1)};
$('#s-save').onclick=async()=>{await api('settings',{log_dir:$('#s-log').value,schem_dir:$('#s-sch').value});toast('تم الحفظ')};

/* ---------- الرئيسية ---------- */
$('#h-quick').innerHTML=[['device','معلومات الجهاز','usb'],['monitor','Software Monitor','dl'],['panic','تحليل Panic','act']].map(x=>`<button class="btn" data-v="${x[0]}" style="padding:18px;font-size:15px">${ic(x[2])} ${x[1]}</button>`).join('');
api('settings').then(s=>$('#h-db').textContent=s.db_count);
bbLogInit();
/* ---------- Diagnostics / IORegistry ---------- */
const DG={raw:''};
function dgSync(){const c=$('#dg-cmd').value,a=$('#dg-arg');$('#dg-plane').style.display=c==='ioreg'?'':'none';a.style.display=c==='ioreg'?'none':'';a.placeholder={ioregentry:'اسم العنصر، مثال: AppleSmartBattery',diagnostics:'النوع: All أو WiFi أو GasGauge أو NAND (فارغ = All)',mobilegestalt:'مفاتيح مفصولة بمسافة'}[c]||''}
function dgRender(){
  const f=$('#dg-filter').value.trim().toLowerCase();
  let L=DG.raw?DG.raw.split('\n'):[];const total=L.length;
  if(f)L=L.filter(l=>l.toLowerCase().includes(f));
  let t=L.join('\n'),cut=false;if(t.length>400000){t=t.slice(0,400000);cut=true}
  $('#dg-out').value=t;
  $('#dg-cnt').textContent=DG.raw?(f?L.length+' من '+total+' سطر':total+' سطر')+(cut?' (معروض أول 400 ألف حرف)':''):'';
}
async function dgRun(){
  const cmd=$('#dg-cmd').value,arg=cmd==='ioreg'?$('#dg-plane').value:$('#dg-arg').value.trim();
  const b=$('#dg-run');b.disabled=true;$('#dg-msg').textContent='جاري القراءة من الهاتف…';
  const r=await api('diag/run',{cmd,arg});b.disabled=false;
  if(!r.ok){$('#dg-msg').innerHTML='<span class="bad" dir="auto">'+esc(r.error||'فشل')+'</span>'+(r.text?'':'');if(r.text){DG.raw=r.text;dgRender()}return}
  DG.raw=r.text;dgRender();
  $('#dg-msg').innerHTML='تم: <span dir="ltr">'+esc(r.cmdline)+'</span> — '+r.size+' حرف'+(r.truncated?' (المعروض مقصوص، الملف المحفوظ كامل)':'')+(r.saved?'<br>محفوظ في: <span dir="ltr">'+esc(r.saved)+'</span>':'');
}
function dgDl(names){
  api('diag/file',{names}).then(r=>{
    if(!r.ok)return toast(r.error||'فشل');
    const bin=atob(r.b64),a=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)a[i]=bin.charCodeAt(i);
    const bl=new Blob([a],{type:r.name.endsWith('.zip')?'application/zip':'text/plain'});
    const l=document.createElement('a');l.href=URL.createObjectURL(bl);l.download=r.name;document.body.appendChild(l);l.click();l.remove();toast('تم التنزيل: '+r.name);
  });
}
function dgBBRender(r){
  const c=$('#dg-bbcard'),o=$('#dg-bbres');c.style.display='';
  const pl=(r.planes||[]).map(p=>'<div class="mut" style="font-size:12.5px">Plane <b>'+esc(p.plane)+'</b>: '+(p.nodes==null?'عدد العناصر غير معروف':p.nodes+' عنصر مقروء')+' — '+p.count+' تطابق — <span dir="ltr">'+esc(p.saved||'')+'</span></div>').join('');
  const thin=(r.planes||[]).filter(p=>p.nodes!=null&&p.nodes<=1).map(p=>p.plane);
  const nm=(r.planes||[]).map(p=>(p.saved||'').split(/[\\/]/).pop()).filter(Boolean);
  window.DG_LAST=nm;
  let h='<div dir="rtl">'+pl+(nm.length?'<div style="margin-top:8px"><button class="btn" style="flex-direction:row" onclick="dgDl(window.DG_LAST)">تنزيل ملفات القراءة (ZIP)</button></div>':'')+(thin.length?'<div class="bad" style="margin-top:6px">تنبيه: الأداة رجعت عنصراً واحداً فقط (الجذر) في: '+esc(thin.join('، '))+'. غياب النتائج هنا لا يعني غياب المودم.</div>':'')+(r.errors&&r.errors.length?'<div class="mut" style="margin-top:6px">أخطاء: '+esc(r.errors.join(' | '))+'</div>':'')+'</div><div style="margin-top:10px">';
  if(!r.count){h+='<div class="empty" dir="rtl">لم يُعثر على عناصر تطابق ('+esc(r.kw)+') في القراءات أعلاه.</div>'}
  else{h+='<div class="mut" dir="rtl" style="margin-bottom:8px">'+r.count+' عنصر يطابق</div>'+r.matches.map(m=>{
    const ps=Object.entries(m.props||{});
    return '<details style="border-bottom:1px solid rgba(80,150,255,.15);padding:6px 4px"><summary><b>'+esc(m.name||'—')+'</b> <span class="mut">'+esc(m.class)+' · '+esc(m.plane||'')+'</span></summary><div class="mut" style="font-size:12px;margin:4px 0 6px">'+esc(m.path)+'</div>'+(ps.length?'<pre style="white-space:pre-wrap;margin:0;font-size:12.5px">'+esc(ps.map(([k,v])=>k+' = '+v).join('\n'))+'</pre>':'<div class="mut">بدون خصائص بسيطة</div>')+'</details>'}).join('')}
  h+='</div>';o.innerHTML=h;
}
async function dgBB(){
  const b=$('#dg-bb');b.disabled=true;$('#dg-msg').textContent='جاري قراءة IORegistry كامل… (3 planes، قد تأخذ عدة دقائق)';
  const r=await api('diag/bbscan',{kw:$('#dg-kw').value});b.disabled=false;
  if(!r.ok){$('#dg-msg').innerHTML='<span class="bad" dir="auto">'+esc(r.error||'فشل')+'</span>'+(r.saved?'<br>محفوظ في: <span dir="ltr">'+esc(r.saved)+'</span>':'');
    if(r.heads){const c=$('#dg-bbcard');c.style.display='';$('#dg-bbres').innerHTML='<div class="mut" dir="rtl">ما رجعته الأداة حرفياً (أول 300 حرف لكل plane):</div>'+r.heads.map(h=>'<div style="margin-top:8px"><b>'+esc(h.plane)+'</b><pre style="white-space:pre-wrap;margin:4px 0 0;font-size:12.5px">'+esc(h.head||'(فارغ)')+'</pre></div>').join('')}
    return}
  $('#dg-msg').textContent='تمت القراءة';
  dgBBRender(r);
}

/*LIVE_BEGIN*/
function liveParse(text){
  const L=text.split(/\r?\n/),rx=/^([A-Z][a-z]{2}) +(\d+) (\d\d):(\d\d):(\d\d) (\S+) ([^\[\s(]+)(?:\(([^)]*)\))?(?:\[(\d+)\])? <(\w+)>: (.*)$/;
  const out=[];
  for(const ln of L){
    const m=rx.exec(ln);
    if(!m){ if(out.length&&ln.trim())out[out.length-1].raw+='\n'+ln; continue }
    out.push({raw:ln,sod:(+m[3])*3600+(+m[4])*60+(+m[5]),time:m[3]+':'+m[4]+':'+m[5],proc:m[7],sub:m[8]||'',lvl:m[10],msg:m[11]});
  }
  return out;
}
function liveFmt(sod){sod=((Math.round(sod)%86400)+86400)%86400;const h=Math.floor(sod/3600),m=Math.floor(sod%3600/60),s=sod%60;return String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+String(s).padStart(2,'0')}
function liveAnalyze(files,kwSrc){
  // files: [{name,text}]
  let kw=null;try{kw=kwSrc?new RegExp(kwSrc,'i'):null}catch(e){kw=null}
  const segs=[],per=[];
  for(const f of files){
    const ls=liveParse(f.text);
    const info={name:f.name,lines:ls.length,first:ls.length?ls[0].time:'',last:ls.length?ls[ls.length-1].time:'',segs:[],tail:[],rep:[],kw:{},panics:[]};
    let cur=null,prev=null,day=0,lastSod=null;
    const rep=new Map(),kwc=new Map(),pan=new Set();
    for(let i=0;i<ls.length;i++){
      const e=ls[i];
      if(lastSod!==null&&e.sod<lastSod-43200)day++;
      lastSod=e.sod;e.abs=day*86400+e.sod;
      if(e.proc==='kernel'){
        const m=/^(\d{6})\.(\d{6}) /.exec(e.msg);
        if(m){
          const c=+m[1]+(+m[2])/1e6;
          if(prev===null||c<prev-5){cur={file:f.name,c0:c,c1:c,t0:e.abs,t1:e.abs,n:0};info.segs.push(cur)}
          cur.c1=c;cur.t1=e.abs;cur.n++;prev=c;
        }
      }
      if(e.lvl==='Error'||e.lvl==='Fault'||/fail|timeout|not ready|error/i.test(e.msg)){
        const k=(e.proc+' '+e.msg).replace(/0x[0-9a-f]+/gi,'0x#').replace(/\d+(\.\d+)?/g,'#').slice(0,150);
        const r=rep.get(k);
        if(r){r.n++;r.last=e.time}else rep.set(k,{k,n:1,lvl:e.lvl,first:e.time,last:e.time});
      }
      if(kw){const mm=kw.exec(e.raw);if(mm){const key=mm[0].toLowerCase();const r=kwc.get(key);if(r){r.n++;r.last=e.time}else kwc.set(key,{k:mm[0],n:1,first:e.time,last:e.time})}}
      const pm=/panic-[A-Za-z0-9+_.-]*\d{4}-\d\d-\d\d-\d{6}[A-Za-z0-9.]*\.ips/.exec(e.msg);
      if(pm)pan.add(pm[0]);
    }
    info.rep=[...rep.values()].sort((a,b)=>b.n-a.n).slice(0,12);
    info.kw=[...kwc.values()].sort((a,b)=>b.n-a.n);
    info.panics=[...pan];
    info.tail=ls.slice(-40).map(e=>e.raw.slice(0,230));
    for(const s of info.segs){s.boot=s.t1-s.c1;segs.push(s)}
    per.push(info);
  }
  // دمج الجلسات المتطابقة (نفس الإقلاع) من ملفات مختلفة
  segs.sort((a,b)=>a.boot-b.boot);
  const merged=[];
  for(const s of segs){
    const m=merged[merged.length-1];
    if(m&&Math.abs(s.boot-m.boot)<3){m.c1=Math.max(m.c1,s.c1);m.c0=Math.min(m.c0,s.c0);m.t1=Math.max(m.t1,s.t1);m.files.add(s.file)}
    else merged.push({boot:s.boot,c0:s.c0,c1:s.c1,t1:s.t1,files:new Set([s.file])});
  }
  const cycles=[];
  for(let i=1;i<merged.length;i++)cycles.push(merged[i].boot-merged[i-1].boot);
  const ends=merged.map(m=>m.c1);
  return {per,merged:merged.map(m=>({boot:m.boot,c0:m.c0,c1:m.c1,t1:m.t1,files:[...m.files]})),cycles,
    endMin:ends.length?Math.min(...ends):null,endMax:ends.length?Math.max(...ends):null};
}
/*LIVE_END*/
function liveRender(r){
  const o=[];
  o.push('<div class="card"><h3><span class="l">الجلسات (حسب عداد kernel)</span></h3>');
  if(!r.merged.length)o.push('<div class="empty" dir="rtl">ما لقيت أسطر kernel فيها عداد من الشكل 000189.715152. جرّب تسجيل بدون فلتر أو بفلتر kernel.</div>');
  else{
    o.push('<table dir="rtl" style="width:100%;border-collapse:collapse;font-size:13px"><tr style="text-align:right"><th>#</th><th>بداية الإقلاع (تقديري)</th><th>أول عداد</th><th>آخر عداد</th><th>وقت آخر سطر</th><th>الملفات</th></tr>');
    r.merged.forEach((m,i)=>o.push('<tr style="border-top:1px solid rgba(80,150,255,.15)"><td>'+(i+1)+'</td><td dir="ltr">'+liveFmt(m.boot)+'</td><td dir="ltr">'+m.c0.toFixed(1)+' s</td><td dir="ltr"><b>'+m.c1.toFixed(3)+' s</b></td><td dir="ltr">'+liveFmt(m.t1)+'</td><td dir="ltr" style="font-size:11px">'+esc(m.files.join(', '))+'</td></tr>'));
    o.push('</table>');
    if(r.merged.length>1)o.push('<div class="note" dir="rtl">آخر عداد بكل الجلسات: من <b dir="ltr">'+r.endMin.toFixed(3)+'</b> إلى <b dir="ltr">'+r.endMax.toFixed(3)+'</b> ثانية (الفرق '+(r.endMax-r.endMin).toFixed(3)+' ثانية).'+(r.cycles.length?'<br>الفاصل بين بدايات الإقلاع (ثانية): <span dir="ltr">'+r.cycles.map(c=>c.toFixed(0)).join(' ، ')+'</span>':'')+'<br>ملاحظة: تشابه آخر عداد بيدل على توقف بنفس اللحظة فقط إذا الملفات انتهت بانقطاع الجهاز نفسه، مو بتصدير يدوي أو إيقاف التسجيل.</div>');
    else o.push('<div class="note" dir="rtl">جلسة وحدة فقط، فما في فاصل لحسابه. أضف ملفات من جلسات تانية.</div>');
  }
  o.push('</div>');
  r.per.forEach(p=>{
    o.push('<details class="card" style="margin-top:12px"><summary><b dir="ltr">'+esc(p.name)+'</b> <span class="mut">'+p.lines+' سطر · '+esc(p.first)+' → '+esc(p.last)+'</span></summary>');
    if(p.panics.length)o.push('<div class="note" dir="ltr">panic reports مذكورة باللوغ: '+esc(p.panics.join(' , '))+'</div>');
    if(p.kw.length)o.push('<div style="margin-top:8px" dir="rtl"><b>كلمات للعدّ</b><table dir="ltr" style="width:100%;font-size:12.5px">'+p.kw.map(k=>'<tr><td>'+esc(k.k)+'</td><td>'+k.n+'</td><td>'+esc(k.first)+' → '+esc(k.last)+'</td></tr>').join('')+'</table></div>');
    if(p.rep.length)o.push('<div style="margin-top:8px" dir="rtl"><b>أكثر الرسائل تكراراً (Error/Fault أو تحتوي fail/timeout/not ready/error)</b><table dir="ltr" style="width:100%;font-size:12px">'+p.rep.map(k=>'<tr><td>'+k.n+'</td><td>'+esc(k.first)+' → '+esc(k.last)+'</td><td style="word-break:break-all">'+esc(k.k)+'</td></tr>').join('')+'</table></div>');
    o.push('<div style="margin-top:8px" dir="rtl"><b>آخر 40 سطر بالملف</b><pre dir="ltr" style="white-space:pre-wrap;font-size:11.5px;margin:4px 0 0">'+esc(p.tail.join('\n'))+'</pre></div></details>');
  });
  return o.join('');
}
async function liveRun(){
  const fl=[...($('#lv-files').files||[])];
  if(!fl.length){$('#lv-msg').textContent='اختر ملف لوغ أول.';return}
  $('#lv-msg').textContent='جاري القراءة…';
  const files=[];
  for(const f of fl){files.push({name:f.name,text:await f.text()})}
  const r=liveAnalyze(files,$('#lv-kw').value);
  $('#lv-res').innerHTML=liveRender(r);
  $('#lv-msg').textContent='تم تحليل '+files.length+' ملف.';
}
$('#lv-run').onclick=liveRun;
function b64dl(name,b64,type){const bin=atob(b64),a=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)a[i]=bin.charCodeAt(i);const l=document.createElement('a');l.href=URL.createObjectURL(new Blob([a],{type}));l.download=name;document.body.appendChild(l);l.click();l.remove()}
async function slStatus(){
  const r=await api('syslog/status',{});if(!r||r.error)return;
  $('#sl-state').textContent=r.on?'● شغّال':'متوقف';
  $('#sl-msg').innerHTML=esc(r.msg||'')+(r.on||r.lines?'<br><span dir="ltr">أسطر محفوظة: '+r.lines+' · آخر سطر بساعة الجهاز: '+esc(r.t_last||'—')+' · آخر عداد: '+(r.c_last==null?'—':r.c_last.toFixed(3))+' s</span>':'')+(r.dir?'<br><span class="mut" dir="ltr">'+esc(r.dir)+'</span>':'');
  const ss=r.sessions||[];
  $('#sl-sess').innerHTML=ss.length?'<table style="width:100%;font-size:12.5px;border-collapse:collapse"><tr style="text-align:right"><th>مقطع</th><th>بداية</th><th>نهاية</th><th>آخر سطر بساعة الجهاز</th><th>آخر عداد</th><th>أسطر</th><th>سبب الإغلاق</th></tr>'+ss.map(s=>'<tr dir="ltr" style="border-top:1px solid rgba(80,150,255,.15)"><td>'+s.n+'</td><td>'+esc(s.start)+'</td><td>'+esc(s.end)+'</td><td>'+esc(s.dev_last||'—')+'</td><td><b>'+(s.c_last==null?'—':s.c_last.toFixed(3))+'</b></td><td>'+s.lines+'</td><td dir="rtl">'+esc(s.reason||'')+'</td></tr>').join('')+'</table>':'';
  return r;
}
$('#sl-start').onclick=async()=>{await api('syslog/start',{kernel_only:$('#sl-kernel').checked});slStatus();clearInterval(window._slT);window._slT=setInterval(slStatus,2000)};
$('#sl-stop').onclick=async()=>{await api('syslog/stop',{});clearInterval(window._slT);slStatus()};
$('#sl-dl-tail').onclick=async()=>{const r=await api('syslog/download',{which:'tails'});if(!r.ok)return toast(r.error||'فشل');b64dl(r.name,r.b64,'application/zip');toast('تم: '+r.name)};
$('#sl-dl-full').onclick=async()=>{const r=await api('syslog/download',{which:'full'});if(!r.ok)return toast(r.error||'فشل');b64dl(r.name,r.b64,'application/zip');toast('تم: '+r.name)};

$('#dg-cmd').onchange=dgSync;$('#dg-run').onclick=dgRun;$('#dg-bb').onclick=dgBB;$('#dg-filter').oninput=dgRender;
$('#dg-copy').onclick=async()=>{const t=$('#dg-out').value;if(!t)return toast('لا يوجد ناتج');try{await navigator.clipboard.writeText(t);toast('تم النسخ')}catch(_){$('#dg-out').select();document.execCommand('copy');toast('تم النسخ')}};
dgSync();
$('#st-right').textContent='Fault DB: Local  |  Build: 10.11.2026';

{const h=location.hash.slice(1);show(document.getElementById('v-'+h)?h:'device')}
pollDev();pollMon();setInterval(pollMon,1000);setInterval(pollDev,5000);

(function(){const C=2*Math.PI*50;function rg(f,r,pc){const e=$(f),k=$(r);if(!e||!k)return;k.style.strokeDasharray=C;const u=()=>{const v=Math.max(0,Math.min(100,parseFloat(e.style.width)||0));k.style.strokeDashoffset=C*(1-v/100);k.style.stroke=(r==='#b-ring'&&v<=20)?'#ff4d5e':'';if(pc){const t=$(pc);if(t)t.textContent=v?Math.round(v)+'%':'—'}};new MutationObserver(u).observe(e,{attributes:true,attributeFilter:['style']});u()}
rg('#b-fill','#b-ring');rg('#s-fill','#s-ring','#s-pc')})();
</script>
<script>(function(){var box=null,mode='';function draw(r){if(r&&r.active){if(box){box.remove();box=null}return}
var nm=(r&&r.email)?'wait':'form';if(nm===mode&&box)return;mode=nm;
if(!box){box=document.createElement('div');box.style.cssText='position:fixed;inset:0;z-index:99999;background:#050d1a;display:flex;align-items:center;justify-content:center;padding:20px';document.body.appendChild(box)}
if(r&&r.email){box.innerHTML='<div dir="rtl" style="max-width:420px;width:100%;text-align:center;color:#e8f1ff"><h2>بانتظار الموافقة</h2><p>تم تسجيل بريدك:</p><div dir="ltr" style="font-size:20px;font-weight:800;color:#ffd479;margin:8px 0">'+r.email+'</div><div style="color:#8ea8cc;font-size:14px;margin-bottom:8px">رمز جهازك: <b dir="ltr">'+(r.code||'')+'</b></div><p>سيفعّل صاحب البرنامج حسابك قريباً، ويبدأ البرنامج تلقائياً.</p></div>';return}
box.innerHTML='<div dir="rtl" style="max-width:420px;width:100%;text-align:center;color:#e8f1ff"><h2>تسجيل الدخول</h2><p>اكتب بريدك الإلكتروني للتسجيل:</p><input id="lic-em" dir="ltr" type="email" placeholder="Email" style="width:100%;padding:10px;border-radius:10px;border:1px solid #2a4a7a;background:#071426;color:inherit;text-align:center;font-size:18px"><button id="lic-go" style="margin-top:12px;padding:10px 22px;border-radius:10px;border:0;background:#2f9bff;color:#fff;font-size:16px;cursor:pointer">Register</button><div id="lic-m" style="margin-top:10px;color:#ff8a8a"></div></div>';
document.getElementById('lic-go').onclick=function(){api('license/register',{email:document.getElementById('lic-em').value}).then(function(x){if(x&&x.ok)chk();else document.getElementById('lic-m').textContent=(x&&x.error)||'خطأ'})}}
function chk(){api('license/status').then(function(r){var was=!!box;draw(r);if(r&&r.active){if(was)location.reload()}else setTimeout(chk,8000)})}
function showUpd(u){var bs='background:#2f6bff;color:#fff;border:0;border-radius:8px;padding:7px 14px;font:inherit;cursor:pointer';var b=document.createElement('div');b.dir='rtl';b.style.cssText='position:fixed;top:14px;left:calc(50% - 48px);transform:translateX(-50%);z-index:99998;background:#0e2a52;border:1px solid #2f6bff;color:#e8f1ff;padding:10px 16px;border-radius:12px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;justify-content:center;box-shadow:0 8px 30px rgba(0,0,0,.5);font:15px Segoe UI,Tahoma,sans-serif;max-width:92vw';
b.innerHTML='<span>يتوفر تحديث جديد: <b dir="ltr">v'+u.latest+'</b></span><button id="upd-go" style="'+bs+'">Update</button><button id="upd-x" style="'+bs+';background:#27405f">Later</button>';document.body.appendChild(b);
document.getElementById('upd-x').onclick=function(){b.remove()};
document.getElementById('upd-go').onclick=function(){api('update/open',{});b.innerHTML='<div style="line-height:1.9;max-width:440px">بدأ تحميل التحديث في المتصفح.<br>1) أغلق البرنامج.<br>2) فك ضغط الملف الجديد <b>فوق مجلد البرنامج الحالي</b> واختر استبدال الملفات.<br>3) شغّل NawafTeam.exe (تسجيلك يبقى محفوظاً).</div><button id="upd-ok" style="'+bs+'">حسناً</button>';document.getElementById('upd-ok').onclick=function(){b.remove()}}}
function updMsg(t){var m=document.createElement('div');m.dir='rtl';m.style.cssText='position:fixed;bottom:44px;right:14px;z-index:99998;background:#0e2a52;border:1px solid #2f6bff;color:#e8f1ff;padding:10px 14px;border-radius:10px;font:14px Segoe UI,Tahoma,sans-serif;max-width:420px;line-height:1.7';m.innerHTML=t;document.body.appendChild(m);setTimeout(function(){m.remove()},12000)}
function upd(manual){api('update/check',manual?{force:1}:undefined).then(function(u){if(u&&u.available){showUpd(u);return}if(!manual)return;if(u&&u.ok)updMsg('أنت على آخر إصدار (v'+u.current+')');else updMsg('تعذّر التحقق من التحديث (v'+(u&&u.current||'?')+')<div dir="ltr" style="font-size:12px;color:#ffb3bb;margin-top:4px;word-break:break-all">'+((u&&u.err)||'')+'</div>')}).catch(function(){if(manual)updMsg('تعذّر التحقق من التحديث')})}
var ub=document.createElement('button');ub.textContent='Check for Update';ub.style.cssText='position:fixed;bottom:6px;left:calc(50% - 48px);transform:translateX(-50%);z-index:99997;background:transparent;color:#8ea8cc;border:0;font:12px Segoe UI,Tahoma,sans-serif;cursor:pointer;text-decoration:underline';ub.onclick=function(){upd(true)};document.body.appendChild(ub);
setTimeout(function(){upd(false)},3000);
setInterval(function(){if(box)return;api('license/status').then(function(r){if(r&&r.active===false){draw(r);setTimeout(chk,8000)}}).catch(function(){})},60000);
chk()})();</script>
</body>
</html>
'''

# ---------------------------------------------------------------- المسارات
import hashlib as _hl2
_FB = "https://nawaf-team-default-rtdb.europe-west1.firebasedatabase.app"
_LF = os.path.join(os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__)), "nawaf_license.json")
_LC = {"t": 0, "v": None}
_DK = {"k": ""}
_DCHK = "47bc446e5c5ac4ab"
def _dk_valid(k):
    import hmac as _hm
    return len(k) >= 16 and _hm.new(k.encode(), b"nt-chk", _hl2.sha256).hexdigest()[:16] == _DCHK
def _dec(b64s, key):
    import hmac as _hm, base64 as _b6
    raw = _b6.b64decode(b64s)
    out = bytearray()
    for i in range(0, len(raw), 32):
        ks = _hm.new(key.encode(), b"nt-ctr" + (i // 32).to_bytes(8, "big"), _hl2.sha256).digest()
        out += bytes(a ^ b for a, b in zip(raw[i:i + 32], ks))
    return bytes(out)
_DV = {"v": ""}
def _dev():
    if _DV["v"]:
        return _DV["v"]
    g = ""
    try:
        import winreg
        _p = chr(92).join(["SOFTWARE", "Microsoft", "Cryptography"])
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _p, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as _k:
            g = str(winreg.QueryValueEx(_k, "MachineGuid")[0])
    except Exception:
        g = ""
    if not g:
        import uuid
        g = "mac" + str(uuid.getnode())
    _DV["v"] = _hl2.sha256(("nt1|" + g).encode()).hexdigest()[:16]
    return _DV["v"]
def _lkey(e):
    return _hl2.sha256((e.strip().lower() + "|" + _dev()).encode()).hexdigest()[:40]
def _lfb(method, e, sub, data=None):
    import urllib.request as _ur
    _rq = _ur.Request(_FB + "/users/" + _lkey(e) + "/" + sub + ".json", method=method, data=(json.dumps(data).encode() if data is not None else None), headers={"Content-Type": "application/json"})
    with _ur.urlopen(_rq, timeout=6) as _r:
        return json.loads(_r.read().decode() or "null")
_APP_REL = "1.8"
_UC = {"t": 0, "v": None}
def _vt(s):
    import re as _re
    return tuple(int(x) for x in _re.findall(r"\d+", str(s))[:4]) or (0,)
def _gh_get(url, hdr, want_url=False):
    import urllib.request as _ur, ssl as _ssl
    errs = []
    modes = ["default", "noproxy", "nocheck"]
    for m in modes:
        try:
            hs = []
            if m == "noproxy":
                hs.append(_ur.ProxyHandler({}))
            if m == "nocheck":
                _c = _ssl._create_unverified_context()
                hs.append(_ur.HTTPSHandler(context=_c))
            op = _ur.build_opener(*hs)
            with op.open(_ur.Request(url, headers=hdr), timeout=15) as _r:
                if want_url:
                    return _r.geturl(), ""
                return _r.read().decode("utf-8", "replace"), ""
        except Exception as e:
            rs = getattr(e, "reason", None) or e
            errs.append(m + ":" + type(e).__name__ + "/" + type(rs).__name__ + " " + str(rs)[:80])
    return None, " ; ".join(errs)
def _upd_fetch():
    import re as _re
    err = ""
    u, e1 = _gh_get("https://github.com/gafaralshekh-gif/nawaf-team/releases/latest", {"User-Agent": "Mozilla/5.0 NawafTeam"}, True)
    if u:
        m = _re.search(r"/releases/tag/([^/?#]+)", u)
        if m:
            tg = m.group(1)
            return tg, "https://github.com/gafaralshekh-gif/nawaf-team/releases/download/" + tg + "/NawafTeam.zip", ""
        e1 = "page: no tag"
    t, e2 = _gh_get("https://api.github.com/repos/gafaralshekh-gif/nawaf-team/releases/latest", {"User-Agent": "NawafTeam", "Accept": "application/vnd.github+json"})
    if t:
        try:
            j = json.loads(t)
            tag = str(j.get("tag_name", ""))
            if tag:
                url = ""
                for a in j.get("assets", []):
                    if str(a.get("name", "")).lower().endswith(".zip"):
                        url = a.get("browser_download_url", "")
                        break
                return tag, url or j.get("html_url", ""), ""
            e2 = "API: no tag"
        except Exception as e:
            e2 = "API: bad json"
    return "", "", "page[" + e1 + "] api[" + e2 + "]"
def _upd_check(force=False):
    now = time.time()
    if not force and _UC["v"] is not None and now - _UC["t"] < (600 if _UC["v"].get("ok") else 60):
        return _UC["v"]
    tag, url, err = _upd_fetch()
    if tag:
        v = {"ok": True, "current": _APP_REL, "latest": tag.lstrip("vV"), "available": _vt(tag) > _vt(_APP_REL), "url": url}
    else:
        v = {"ok": False, "available": False, "current": _APP_REL, "err": err}
    _UC["t"], _UC["v"] = now, v
    return v
_LM = {"d": {}}
def _lload():
    for _i in range(3):
        try:
            d = json.load(open(_LF, encoding="utf-8"))
            if isinstance(d, dict):
                _LM["d"] = dict(d)
                return d
        except Exception:
            time.sleep(0.05)
    return dict(_LM["d"])
def _lsave(d):
    _LM["d"] = dict(d)
    try:
        _tmp = _LF + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            json.dump(d, _f)
        os.replace(_tmp, _LF)
    except Exception:
        pass
def _lic_state():
    d = _lload()
    e = d.get("email", "")
    if not e:
        return (False, "", False)
    now = time.time()
    if _LC["v"] is not None and now - _LC["t"] < (180 if _LC["v"][0] else 4):
        return _LC["v"]
    try:
        _v = _lfb("GET", e, "ok")
        _sv = str(_v).strip()
        ok = _v is True or _sv.lower() in ("true", "1", "yes")
        _kv = isinstance(_v, str) and _dk_valid(_sv)
        if _kv:
            ok = True
            _DK["k"] = _sv
            if d.get("dk") != _sv:
                d["dk"] = _sv
                _lsave(d)
        elif not ok:
            _DK["k"] = ""
            _BASE.clear()
            if d.get("dk"):
                d.pop("dk", None)
                _lsave(d)
        if not ok and _v is None and not _LC.get("rq"):
            _LC["rq"] = 1
            try:
                _lfb("PUT", e, "req", {"email": e, "ts": int(time.time())})
            except Exception:
                pass
        if ok and now - d.get("last_ok", 0) > 600:
            d["last_ok"] = now
            _lsave(d)
        r = (ok, e, not ok)
    except Exception:
        _g = now - d.get("last_ok", 0) < 3 * 86400
        if _g and not _DK["k"] and _dk_valid(str(d.get("dk", ""))):
            _DK["k"] = d["dk"]
        r = (_g, e, False)
    _LC["t"], _LC["v"] = now, r
    return r
def _lic_register(e):
    e = e.strip().lower()
    if "@" not in e or len(e) > 100:
        return {"ok": False, "error": "اكتب إيميلاً صحيحاً"}
    try:
        try:
            _lfb("PUT", e, "req", {"email": e, "ts": int(time.time())})
        except Exception as ex:
            if "401" not in str(ex) and "403" not in str(ex):
                raise
        _lsave({"email": e})
        _LC["v"] = None
        return {"ok": True}
    except Exception:
        return {"ok": False, "error": "تعذر الاتصال بالإنترنت"}

def route(method, path, q, body):
    if path == "license/status":
        _s = _lic_state()
        return {"ok": True, "active": _s[0], "email": _s[1], "pending": _s[2], "code": (_lkey(_s[1])[:6] if _s[1] else "")}
    if path == "license/register":
        return _lic_register(str(body.get("email", "")))
    if path == "update/check":
        return _upd_check(bool(body.get("force")) if isinstance(body, dict) else False)
    if path == "update/open":
        _u = str(_upd_check().get("url", ""))
        if _u.startswith("https://github.com/gafaralshekh-gif/"):
            try:
                webbrowser.open(_u)
            except Exception:
                return {"ok": False}
        return {"ok": True}
    if not _lic_state()[0]:
        return {"ok": False, "error": "غير مفعّل", "locked_lic": True}
    cfg = cfg_load()
    if path.startswith("flash/"):
        _fr = flash_route(path, q, body)
        if _fr is not None:
            return _fr
    if path == "device":
        return get_device()
    if path == "monitor":
        return MON.snapshot(int(q.get("since", ["0"])[0] or 0))
    if path == "monitor/start":
        folder = body.get("folder") or cfg.get("log_dir", "")
        if not folder or not os.path.isdir(folder):
            return {"ok": False, "error": "حدد مجلد سجلات صحيح من الإعدادات أو تبويب الأدوات."}
        MON.start(folder)
        return {"ok": True}
    if path == "bblive/status":
        return {"ok": True, "on": bool(SL["on"]), "msg": SL["msg"], "total": BBL["total"], "kinds": len(BBL["ev"]), "since": BBL["since"], "lines": SL["lines"], "top": sorted(BBL["ev"].values(), key=lambda e: -e["n"])[:15]}
    if path == "bblive/analyze":
        if not BBF["armed"] and BBF["state"] != "run":
            BBF["armed"] = True
            threading.Thread(target=bbf_run, daemon=True).start()
        return bbl_analyze()
    if path == "bblive/bbfiles":
        return {"ok": True, "state": BBF["state"], "n": BBF["n"], "files": BBF["files"], "dest": BBF.get("dest", "")}
    if path == "bblive/reset":
        BBL["alt"].clear(); BBL["ev"].clear(); BBL["bt"].clear(); BBL["stat"].clear(); BBL["rdy"].update(f=0, t=0); BBL["mk"].update(pcie=0, cc=0, mbu=0, cpub=0, ping=0, crash=0, qmi=0, odr=0, linkdown=0, offline=0, portto=0, e51=0, lpf=0, lbw=0, trerr=0, fpm=0); BBL_SEEN.clear(); BBF.update(state="", armed=False, n=0, files=[]); BBL["total"] = 0; BBL["since"] = time.strftime("%H:%M:%S"); cmpl_reset()
        return {"ok": True}
    if path == "device/restart":
        if DEMO["on"]:
            return {"ok": True, "msg": "demo"}
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        def _ok(cmd, t):
            try:
                return subprocess.run(cmd, capture_output=True, text=True, timeout=t, **kw).returncode == 0
            except Exception:
                return False
        done = False
        exe = find_tool("idevicediagnostics")
        if exe:
            done = _ok([exe, "restart"], 20)
        if not done and _pmd3_ok():
            done = _ok([sys.executable, "-m", "pymobiledevice3", "diagnostics", "restart"], 40)
        return {"ok": done, "msg": "Restart command sent." if done else "Could not restart from the computer (idevicediagnostics not found or device not ready). Restart the phone manually."}
    if path == "bb/models":
        seen, out = set(), []
        for pt, nm in PRODUCT_NAMES.items():
            out.append({"pt": pt, "name": nm})
        out.sort(key=lambda x: (x["name"], x["pt"]))
        return {"models": out}
    if path == "bb/start":
        if BB["state"] == "running":
            return {"ok": False, "error": "التحليل شغّال حالياً."}
        threading.Thread(target=bb_job, args=(str(body.get("model", "")), bool(body.get("restart")), int(body.get("secs") or 30)), daemon=True).start()
        return {"ok": True}
    if path == "bb/logfile":
        txt = str(body.get("text", ""))
        if len(txt) > 40_000_000:
            return {"ok": False, "error": "الملف أكبر من 40 ميغابايت."}
        return analyze_log_text(txt, str(body.get("name", "ملف"))[:80])
    if path == "bb/status":
        return dict(BB)
    if path == "bb/notes":
        pt = str(body.get("pt", "")).strip()
        if not pt:
            return {"ok": False}
        notes = dict(cfg.get("model_notes") or {})
        notes[pt] = str(body.get("text", ""))[:5000]
        cfg["model_notes"] = notes
        cfg_save(cfg)
        return {"ok": True}
    if path == "bb/baseline":
        dev = get_device()
        mo = dev.get("modem") if dev.get("connected") and dev.get("mode") == "Normal" else None
        if not mo:
            return {"ok": False, "error": "وصّل الجهاز السليم وهو شغّال (Normal) وموثوق، ثم أعد المحاولة."}
        pt = (dev.get("fields") or {}).get("Model Identifier", "")
        bl = dict(cfg.get("baselines") or {})
        bl[pt] = {"keys": bb_profile(mo.get("keys") or {}), "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "name": dev.get("name", "")}
        cfg["baselines"] = bl
        cfg_save(cfg)
        return {"ok": True, "pt": pt, "count": len(bl[pt]["keys"])}
    if path == "flow/get":
        return flow_get(str(body.get("pt", "")))
    if path == "flow/img":
        return flow_img_set(str(body.get("pt", "")), str(body.get("node", "")), str(body.get("data", "")))
    if path == "flow/save":
        return flow_save(str(body.get("pt", "")), body, cfg)
    if path == "flow/log":
        return {"items": (cfg.get("measure_log") or {}).get(str(body.get("pt", "")), [])}
    if path == "bb/notes/get":
        return {"text": (cfg.get("model_notes") or {}).get(str(body.get("pt", "")), "")}
    if path == "modem/scan":
        return modem_scan()
    if path == "monitor/winread":
        time.sleep(min(15, max(0, int(body.get("delay", 0) or 0))))
        txt, n, err = read_3utools_window()
        return {"ok": True, "text": txt, "items": n, "err": err}
    if path == "monitor/winstart":
        if platform.system() != "Windows":
            return {"ok": False, "error": "هذه الميزة تعمل على Windows فقط."}
        MON.start_win(); return {"ok": True}
    if path == "monitor/stop":
        MON.stop(); return {"ok": True}
    if path == "monitor/reset":
        MON.reset(); return {"ok": True}
    if path == "monitor/demo":
        run_demo(); return {"ok": True}
    if path == "monitor/analyze":
        MON.reset()
        for ln in str(body.get("text", ""))[:2_000_000].splitlines():
            MON.feed(ln)
        return {"ok": True}
    if path == "panicwatch/start":
        if not (PW["thread"] and PW["thread"].is_alive()):
            PW["on"] = True
            t = threading.Thread(target=pw_loop, daemon=True)
            PW["thread"] = t
            t.start()
        return {"ok": True}
    if path == "panicwatch/stop":
        PW["on"] = False
        return {"ok": True}
    if path == "panicwatch/status":
        return {"ok": True, "on": bool(PW["on"] and PW["thread"] and PW["thread"].is_alive()), "attempts": PW["attempts"], "msg": PW["msg"], "log": PW["log"][-10:], "files": pw_list(), "devtool": PW["devtool"], "folder": pw_dest()}
    if path == "panicwatch/download":
        import base64, zipfile, io
        fl = find_panics(pw_dest())[:10]
        if not fl:
            return {"ok": False, "error": "ما في ملفات panic مسحوبة بعد."}
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
            for f in fl:
                z.write(f, os.path.basename(f))
        return {"ok": True, "name": "panic_files.zip", "b64": base64.b64encode(bio.getvalue()).decode()}
    if path == "syslog/start":
        SL["kernel_only"] = bool(body.get("kernel_only", True))
        if not (SL["thread"] and SL["thread"].is_alive()):
            SL["on"] = True
            SL["sessions"] = []
            BBL["alt"].clear(); BBL["ev"].clear(); BBL["bt"].clear(); BBL["stat"].clear(); BBL["rdy"].update(f=0, t=0); BBL["mk"].update(pcie=0, cc=0, mbu=0, cpub=0, ping=0, crash=0, qmi=0, odr=0, linkdown=0, offline=0, portto=0, e51=0, lpf=0, lbw=0, trerr=0, fpm=0); BBL_SEEN.clear(); BBF.update(state="", armed=False, n=0, files=[]); BBL["total"] = 0; BBL["since"] = time.strftime("%H:%M:%S"); cmpl_reset()
            t = threading.Thread(target=sl_loop, daemon=True)
            SL["thread"] = t
            t.start()
        return {"ok": True}
    if path == "syslog/stop":
        SL["on"] = False
        try:
            if SL["proc"]:
                SL["proc"].terminate()
        except Exception:
            pass
        return {"ok": True}
    if path == "syslog/status":
        return {"ok": True, "on": bool(SL["on"] and SL["thread"] and SL["thread"].is_alive()), "msg": SL["msg"], "lines": SL["lines"], "c_last": SL["c_last"], "t_last": SL["t_last"], "tool": SL["tool"], "sessions": SL["sessions"][-12:], "dir": SL["run_dir"]}
    if path == "syslog/download":
        import base64, zipfile, io
        d = SL["run_dir"]
        if not d or not os.path.isdir(d):
            return {"ok": False, "error": "ما في تسجيل بعد."}
        full = str(body.get("which", "tails")) == "full"
        names = sorted(f for f in os.listdir(d) if (f.endswith(".log") if full else f.startswith("tail_")))
        if not names:
            return {"ok": False, "error": "ما في ملفات بعد."}
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
            for f in names:
                z.write(os.path.join(d, f), f)
        return {"ok": True, "name": ("syslog_full.zip" if full else "syslog_tails.zip"), "b64": base64.b64encode(bio.getvalue()).decode()}
    if path == "diag/file":
        import base64, zipfile, io
        d = os.path.join(os.path.expanduser("~"), "NawafTeam_logs")
        names = body.get("names") or []
        ok_names = []
        for n in names[:10]:
            n = os.path.basename(str(n))
            fp = os.path.join(d, n)
            if n.startswith("diag_") and n.endswith(".txt") and os.path.isfile(fp):
                ok_names.append((n, fp))
        if not ok_names:
            return {"ok": False, "error": "الملف غير موجود في مجلد اللوغات (من منظور البرنامج): " + d}
        if len(ok_names) == 1:
            with open(ok_names[0][1], "rb") as fh:
                raw = fh.read()
            return {"ok": True, "name": ok_names[0][0], "b64": base64.b64encode(raw).decode()}
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
            for n, fp in ok_names:
                z.write(fp, n)
        return {"ok": True, "name": "diag_logs.zip", "b64": base64.b64encode(bio.getvalue()).decode()}
    if path == "diag/run":
        r = diag_run(str(body.get("cmd", "")), str(body.get("arg", "")))
        if r.get("ok"):
            lab = r["cmdline"]
            r["saved"] = diag_save(lab, r["text"])
            r["size"] = len(r["text"])
            r["text"] = r["text"][:1_500_000]
            r["truncated"] = r["size"] > 1_500_000
        return r
    if path == "diag/bbscan":
        kw = str(body.get("kw", "")).strip() or "baseband|modem|bbpmu|pcie|commcenter"
        if not _ARG_RX.match(kw.replace("|", " ")):
            return {"ok": False, "error": "كلمات البحث تحتوي رموزاً غير مسموحة"}
        try:
            rx = re.compile("|".join(re.escape(x) for x in kw.split("|") if x.strip()), re.I)
        except Exception:
            return {"ok": False, "error": "كلمات البحث غير صالحة"}
        allm, planes, errs = [], [], []
        for pl in ("IOService", "IODeviceTree", ""):
            r = diag_run("ioreg", pl, timeout=120)
            if not r.get("ok"):
                errs.append((pl or "default") + ": " + str(r.get("error", "")))
                continue
            txt = r["text"]
            saved = diag_save("ioreg_" + (pl or "default"), txt)
            if not _is_plist_out(txt):
                errs.append((pl or "default") + ": الناتج ليس plist مفهوماً")
                planes.append({"plane": pl or "default", "nodes": None, "count": 0, "size": len(txt), "saved": saved, "head": txt[:300], "bad": True})
                continue
            t = diag_tree_matches(txt, rx)
            if t is None:
                lines = [(i + 1, l.strip()) for i, l in enumerate(txt.splitlines()) if rx.search(l)]
                planes.append({"plane": pl or "default", "nodes": None, "count": len(lines), "size": len(txt), "saved": saved, "lines": lines[:100]})
                continue
            for m_ in t["matches"]:
                m_["plane"] = pl or "default"
            allm += t["matches"]
            planes.append({"plane": pl or "default", "nodes": t["nodes"], "count": len(t["matches"]), "size": len(txt), "saved": saved})
        if not planes:
            return {"ok": False, "error": " | ".join(errs) or "فشل القراءة"}
        if all(p_.get("bad") or p_.get("nodes") is None and not p_.get("lines") for p_ in planes):
            return {"ok": False, "error": "لم تُرجع الأداة أي بيانات IORegistry مفهومة، فلم يتم البحث. هذا لا يدل على وجود أو غياب المودم. (الجهاز يجب أن يكون شغّالاً بالوضع العادي ومتصلاً وموثوقاً)", "heads": [{"plane": p_["plane"], "head": p_.get("head", "")} for p_ in planes], "saved": planes[0].get("saved")}
        return {"ok": True, "mode": "tree", "kw": kw, "matches": allm[:300], "count": len(allm), "planes": planes, "errors": errs}
    if path == "panic/raw":
        nm = os.path.basename(str(body.get("name", "")))
        dest = crash_dest()
        hit = None
        for f in glob.glob(os.path.join(dest, "**", "*"), recursive=True):
            if os.path.isfile(f) and os.path.basename(f) == nm:
                hit = f
                break
        if not hit:
            return {"ok": False, "error": "الملف غير موجود على هذا الكمبيوتر (اعمل سكان من جديد)."}
        txt, sz = read_text_any(hit)
        return {"ok": True, "size": sz, "text": txt[:200000], "path": hit}
    if path == "panic":
        return panic_json(str(body.get("text", ""))[:3_000_000])
    if path == "auto/status":
        return {"ok": True, "on": AUTO["on"], "present": AUTO["present"], "state": AUTO["state"], "note": AUTO["note"], "results": AUTO["results"], "pulls": AUTO["pulls"], "msg": AUTO["msg"], "missing": AUTO["missing"], "prog": AUTO["prog"]}
    if path == "auto/toggle":
        AUTO["on"] = bool(body.get("on", True))
        return {"ok": True, "on": AUTO["on"]}
    if path == "auto/now":
        AUTO["seen"].clear(); AUTO["results"] = []
        def _now():
            try:
                auto_pull_once()
                if AUTO["missing"] and not _pmd3_ok():
                    AUTO["state"] = "الأداة الحالية فشلت — عم أثبّت أداة السحب البديلة (pymobiledevice3)، تحتاج إنترنت وقد تستغرق دقائق. لا تغلق البرنامج."
                    ok_i, msg_i = install_pull_tool()
                    AUTO["msg"] = (msg_i or "")[-500:]
                    if ok_i:
                        AUTO["seen"].clear()
                        auto_pull_once()
                    else:
                        AUTO["state"] = "فشل تثبيت الأداة البديلة (تأكد من الإنترنت). التفاصيل أدناه."
            except Exception as e:
                AUTO["state"] = "خطأ: %s" % e
                _prog(0, "", busy=False)
        threading.Thread(target=_now, daemon=True).start()
        return {"ok": True}
    if path == "pull":
        dest = crash_dest()
        if DEMO["on"]:
            return {"ok": True, "msg": "", "results": [{"file": "panic-full-demo.ips", **panic_json('"product" : "iPhone16,2", "panicString" : "panic(cpu 0): SMC PANIC - ASSERT: target\\d84_esim\\target.cpp:192: SMC BSC failure S.sensor array 0 - 3 is 0xa1, 0x0, 0x0, 0x0')}]}
        try:
            ok, msg, miss = pull_crash_reports(dest)
        except Exception as e:
            ok, msg, miss = False, f"تعذر السحب: {e}", False
        res = []
        files, skipped = find_panics(dest, True) if os.path.isdir(dest) else ([], 0)
        files = files[:5]
        for f in files:
            txt, sz = read_text_any(f)
            res.append({"file": os.path.basename(f), "size": sz, "path": f, **panic_json(txt)})
        empty = bool(res) and all(x.get("size", 1) == 0 for x in res)
        if empty:
            ok, res = False, []
            miss = True
        elif not ok and files:
            ok = True
        return {"ok": ok, "msg": msg[-400:], "missing": miss, "results": res, "folder": dest, "skipped": skipped, "empty": empty}
    if path == "pull/install":
        ok, msg = install_pull_tool()
        return {"ok": ok, "msg": msg}
    if path == "db/update":
        try:
            return {"ok": True, "count": db_update()}
        except Exception as e:
            return {"ok": False, "error": f"تعذر التحديث ({type(e).__name__}). تأكد من الإنترنت وجرّب مرة ثانية."}
    if path == "usbdebug":
        tr = []
        try:
            res = usb_apple_mode(tr)
        except Exception as e:
            res = f"error {e!r}"
        return {"os": os.name, "trace": tr, "result": res, "known": known_load()}
    if path == "faults":
        return {"codes": [], "panic": [], "sensors": [], "legacy": [], "hw": []}
    if path == "settings":
        if method == "POST":
            for k in ("log_dir", "schem_dir"):
                if k in body:
                    cfg[k] = str(body[k]).strip()
            if "add_scan_dir" in body:
                d = str(body["add_scan_dir"]).strip().strip('"')
                if not os.path.isdir(d):
                    return {"ok": False, "error": "المجلد غير موجود — تأكد من المسار."}
                cfg["scan_dirs"] = sorted(set(cfg.get("scan_dirs", []) + [d]))
            if body.get("clear_scan_dirs"):
                cfg["scan_dirs"] = []
            cfg_save(cfg)
        return {"ok": True, "scan_dirs": cfg.get("scan_dirs", []), "log_dir": cfg.get("log_dir", ""), "schem_dir": cfg.get("schem_dir", ""),
                "suggest": candidate_log_dirs(), "db_count": 0}
    if path == "schem/list":
        d = cfg.get("schem_dir", "")
        if not d or not os.path.isdir(d):
            return {"ok": False, "error": "حدد مجلد المخططات من الإعدادات.", "files": []}
        files = []
        for dp, dn, fn in os.walk(d):
            for f in fn:
                if f.lower().endswith(SCHEM_EXT):
                    files.append(os.path.join(dp, f))
            if len(files) > 5000:
                break
        return {"ok": True, "files": sorted(files)}
    if path == "schem/open":
        p, d = os.path.realpath(body.get("path", "")), os.path.realpath(cfg.get("schem_dir", "") or "\0")
        if os.path.commonpath([p, d]) == d and os.path.isfile(p):
            open_path(p); return {"ok": True}
        return {"ok": False}
    if path == "openlog":
        d = cfg.get("log_dir", "")
        if d and os.path.isdir(d):
            open_path(d); return {"ok": True}
        return {"ok": False, "error": "مجلد السجل غير محدد."}
    if path == "disc/start":
        DISC["before"] = snapshot(discovery_roots() + cfg.get("scan_dirs", []))
        return {"ok": True, "count": len(DISC["before"])}
    if path == "disc/stop":
        if DISC["before"] is None:
            return {"ok": False, "error": "ابدأ اللقطة الأولى أولاً."}
        res = diff_snapshots(DISC["before"], snapshot(discovery_roots() + cfg.get("scan_dirs", [])))[:300]
        DISC["results"] = [r[0] for r in res]
        return {"ok": True, "items": [{"path": p, "delta": d, "kind": k, "text": looks_textual(p)} for p, d, k in res]}
    if path == "disc/preview":
        p = body.get("path", "")
        if p in DISC["results"] and looks_textual(p):
            with open(p, "rb") as fh:
                fh.seek(max(0, os.path.getsize(p) - 4000))
                return {"ok": True, "text": decode_bytes(fh.read())}
        return {"ok": False, "error": "ملف غير نصي أو غير مسموح."}
    return {"error": "unknown"}

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "NawafTeam"
    def log_message(self, *a):
        pass

    def _send(self, code, data, ctype="application/json; charset=utf-8"):
        b = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _handle(self, method):
        host = self.headers.get("Host", "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return self._send(403, {"error": "host"})
        LAST_HIT[0] = time.time()
        u = urllib.parse.urlparse(self.path)
        if u.path == "/":
            return self._send(200, UI_HTML.replace("__TOKEN__", TOKEN).encode("utf-8"), "text/html; charset=utf-8")
        if not u.path.startswith("/api/") or self.headers.get("X-Token") != TOKEN:
            return self._send(403, {"error": "forbidden"})
        body = {}
        if method == "POST":
            n = int(self.headers.get("Content-Length", 0) or 0)
            if n > 6_000_000:
                return self._send(413, {"error": "too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                body = {}
        try:
            out = route(method, u.path[5:], urllib.parse.parse_qs(u.query), body)
        except Exception as e:
            out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self._send(200, out)

    def do_GET(self): self._handle("GET")
    def do_POST(self): self._handle("POST")

def launch_window(url):
    if os.name == "nt":
        for p in (r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
                  r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                  r"%LocalAppData%\Google\Chrome\Application\chrome.exe"):
            p = os.path.expandvars(p)
            if os.path.exists(p):
                subprocess.Popen([p, "--app=" + url, "--window-size=1320,860"])
                return
    webbrowser.open(url)

CLIP = {"last": 0, "src": ""}
def clip_text():
    import ctypes
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.GetClipboardData.restype = ctypes.c_void_p
    k.GlobalLock.restype = ctypes.c_void_p
    if not u.OpenClipboard(None):
        return ""
    try:
        h = u.GetClipboardData(13)    # CF_UNICODETEXT
        if not h:
            return ""
        p = k.GlobalLock(ctypes.c_void_p(h))
        if not p:
            return ""
        try:
            return ctypes.wstring_at(p)
        finally:
            k.GlobalUnlock(ctypes.c_void_p(h))
    finally:
        u.CloseClipboard()

def clip_loop():
    # ينسخ المستخدم سجل 3uTools (Ctrl+A ثم Ctrl+C) فيُحلَّل تلقائياً دون لصق
    try:
        CLIP["last"] = hash(clip_text())    # ما كان في الحافظة عند التشغيل لا يُلتقط
    except Exception:
        pass
    while True:
        time.sleep(1.2)
        try:
            t = clip_text()
            if len(t) < (300 if re.search(r"Start flash", t) else 1500) or not re.search(r"(?:Checkpoint\s+(?:started|completed)|(?:Started|Completed)\s+checkpoint)\s+id:|Start flash|Waiting for firmware extraction|Flashing completed", t, re.I):
                continue
            h = hash(t)
            if h == CLIP["last"]:
                continue
            CLIP["last"] = h
            MON.stop()
            MON.reset()
            for ln in t[:2_000_000].splitlines():
                MON.feed(ln)
            with MON.lock:
                MON.source = "الحافظة (Clipboard)"
        except Exception:
            pass

def main():
    refs_init()
    a = sys.argv[1:]
    if len(a) == 2 and a[0] == "--panic":
        print(format_panic(analyze_panic(open(a[1], errors="ignore").read()))); return
    if len(a) == 2 and a[0] == "--log":
        for line in open(a[1], errors="ignore"):
            h = analyze_log_line(line)
            if h:
                print(line.strip()[:140]); [print(f"  → [{CAT_AR[c]}] {m}") for c, m in h]
        return
    port = int(a[a.index("--port") + 1]) if "--port" in a else 0
    if "--demo" in a:
        DEMO["on"] = True
        run_demo()
    if platform.system() == "Windows":
        threading.Thread(target=clip_loop, daemon=True).start()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"{APP} v{VER} — {url}\nأغلق النافذة أو اضغط Ctrl+C للإنهاء.")
    if "--no-browser" not in a:
        launch_window(url)
    def _auto():
        try:
            if time.time() - known_load().get("updated", 0) > 30 * 86400:
                db_update()
        except Exception:
            pass
    threading.Thread(target=_auto, daemon=True).start()
    LAST_HIT[0] = time.time()
    if "--keep" not in a:
        def idle():
            while True:
                time.sleep(10)
                if time.time() - LAST_HIT[0] > 180:
                    os._exit(0)
        threading.Thread(target=idle, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
