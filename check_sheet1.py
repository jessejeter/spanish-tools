from forecast import fetch_sheet
words = {"arrancar", "agotarse", "fijarse", "estropearse", "bromear", "apenas", "conjunto", "jornada", "paleta"}
s1 = fetch_sheet("Sheet1"); s2 = fetch_sheet("Sheet2")
print("Sheet1 rows:", len(s1))
for r in s1:
    r = r + [""] * 7
    if r[1].strip() in words: print("  ", r[0], "|", r[1], "|", r[2], "| sense:", r[5], "| ctx:", r[6])
