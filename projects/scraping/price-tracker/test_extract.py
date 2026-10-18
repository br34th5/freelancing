"""Offline tests for extract.py using synthetic list HTML. Run: python3 test_extract.py"""

from extract import parse_list_page, parse_description_text, _parse_price, _parse_time_to_sell

PAGE = """
<html><body>
<a href="/skelbimai/procesorius-ryzen-5-5600-111.html" class="standard-list-item" data-item-id="111" id="ads111">
  <div class="collapsed-info">
    <div class="title">Procesorius Ryzen 5 5600</div>
    <div class="price-item"><div class="price">100 €</div>
      <div class="price-change"><span class="down"></span> 12,5%</div></div>
    <div class="second-line">Vilnius, spalio 01 d.</div>
  </div>
  <div class="extended-info">
    <div class="img-block"><div class="wrapper"><div class="top-right"></div></div></div>
    <div class="content-block">
      <div class="title">Procesorius Ryzen 5 5600</div>
      <div class="second-dataline">Vilnius, spalio 01 d.</div>
      <div class="item-params"><div class="param">Būklė: Naudota</div></div>
      <div class="price-line"><div class="price">100 €</div>
        <div class="price-change"><span class="down"></span> 12,5%</div></div>
    </div>
  </div>
</a>

<a href="/skelbimai/gtx-1080-222.html" class="standard-list-item" data-item-id="222">
  <div class="collapsed-info">
    <div class="title">Gtx 1080 8gb</div>
    <div class="price-item"><div class="price">160 €</div></div>
    <div class="second-line">Kaunas, rugsėjo 20 d.</div>
  </div>
  <div class="extended-info">
    <div class="img-block"><div class="wrapper">
      <div class="top-right"><div class="item sold"><b>Parduota</b> per 4 val.</div></div>
    </div></div>
    <div class="content-block">
      <div class="title">Gtx 1080 8gb</div>
      <div class="price-line"><div class="price">160 €</div></div>
    </div>
  </div>
</a>

<a href="/skelbimai/rtx-4070-reserved-333.html" class="standard-list-item" data-item-id="333">
  <div class="extended-info">
    <div class="img-block"><div class="wrapper">
      <div class="top-right"><div class="item reserved-icon"><b>Rezervuota</b></div></div>
    </div></div>
    <div class="content-block">
      <div class="title">RTX 4070 12GB</div>
      <div class="price-line"><div class="price">450 €</div>
        <div class="price-change"><span class="up"></span> 5%</div></div>
    </div>
  </div>
</a>

<a href="/skelbimai/superku-444.html" class="standard-list-item" data-item-id="444">
  <div class="collapsed-info">
    <div class="title">Superku vaizdo plokštes</div>
    <div class="price-item"></div>
  </div>
  <div class="extended-info">
    <div class="content-block"><div class="title">Superku vaizdo plokštes</div></div>
  </div>
</a>

<div class="pagination">
  <a class="pagination_link" rel="next" href="/skelbimai/kompiuterija/procesoriai-cpu/2">»</a>
</div>
</body></html>
"""


def run() -> int:
    fails = 0

    def check(name, got, want):
        nonlocal fails
        ok = got == want
        print(f"[{'ok ' if ok else 'FAIL'}] {name}: {got!r}" + ("" if ok else f" (want {want!r})"))
        if not ok:
            fails += 1

    # unit helpers
    check("price '1 700 €'", _parse_price("1 700 €"), 1700.0)
    check("price '12€'", _parse_price("12€"), 12.0)
    check("price '1,50 €'", _parse_price("1,50 €"), 1.5)
    check("price empty", _parse_price(""), None)
    check("tts 'per 5 d.'", _parse_time_to_sell("Parduota per 5 d."), 7200)
    check("tts 'per 4 val.'", _parse_time_to_sell("Parduota per 4 val."), 240)
    check("tts 'per 30 min.'", _parse_time_to_sell("Parduota per 30 min."), 30)

    listings, next_url = parse_list_page(PAGE, "https://www.skelbiu.lt/skelbimai/x/", "cpus")
    check("listing count", len(listings), 4)
    check("next page", next_url, "https://www.skelbiu.lt/skelbimai/kompiuterija/procesoriai-cpu/2")

    by_id = {l.item_id: l for l in listings}
    l1 = by_id["111"]
    check("111 price", l1.price_eur, 100.0)
    check("111 change signed", l1.price_change_pct, -12.5)
    check("111 state", l1.state, "active")
    check("111 condition", l1.condition, "Naudota")
    check("111 location", l1.location, "Vilnius, spalio 01 d.")

    l2 = by_id["222"]
    check("222 state", l2.state, "sold")
    check("222 tts minutes", l2.time_to_sell_min, 240)
    check("222 price retained", l2.price_eur, 160.0)

    l3 = by_id["333"]
    check("333 state", l3.state, "reserved")
    check("333 change up", l3.price_change_pct, 5.0)

    l4 = by_id["444"]
    check("444 no price", l4.price_eur, None)
    check("444 active (wanted ad not state)", l4.state, "active")
    check("444 url absolute", l4.url, "https://www.skelbiu.lt/skelbimai/superku-444.html")

    print(f"\n{'PASS' if fails == 0 else f'{fails} FAILURES'}")

    # --- description parsing (label + plain prose passes) ---
    fails2 = 0
    def check2(name, got, want):
        nonlocal fails2
        ok = got == want
        print(f"[{'ok ' if ok else 'FAIL'}] {name}: {got!r}" + ("" if ok else f" (want {want!r})"))
        if not ok:
            fails2 += 1

    # labeled fields (Lithuanian "PROCESORIUS:" / "VAIZDO PLOKŠTĖ:")
    desc_labeled = (
        "Parduodamas labai galingas kompiuteris su periferija.\n"
        "Procesorius (CPU) - AMD Ryzen 7 9800X3D\n"
        "Vaizdo plokštė (GPU) - GeForce RTX 5070 Ti"
    )
    out = parse_description_text(desc_labeled)
    check2("labeled cpu", out.get("processor"), "AMD Ryzen 7 9800X3D")
    check2("labeled gpu", out.get("gpu"), "GeForce RTX 5070 Ti")

    # plain prose — no labels, model buried in Lithuanian sentence
    desc_prose = (
        "Šis kompiuteris yra šiek tiek naudotas. Jo specsai yra 4060 geforce gpu, "
        "Ryzen 7 5800X, Tuf gaming motherboard, 16 GB Ram."
    )
    out = parse_description_text(desc_prose)
    check2("prose gpu contains 4060", "4060" in out.get("gpu", ""), True)
    check2("prose cpu contains Ryzen 7", "Ryzen 7" in out.get("processor", ""), True)

    # prose with VRAM-style noise — should still find the GPU model
    desc_vram = "Parduodu kompiuteri su rtx 3070 8gb ram, veikia puikiai zaidimam."
    out = parse_description_text(desc_vram)
    check2("prose gpu = rtx 3070", "RTX 3070" in out.get("gpu", ""), True)

    # labeled should win over loose (never overwrite a clean labeled value);
    # labeled pass stores raw text, scoring.py later canonicalizes via _join_gpu
    desc_mixed = "GPU: GTX 1660 Ti\nAprašymas: tai galingas RTX 4090 modelis."
    out = parse_description_text(desc_mixed)
    check2("labeled wins over prose", "GTX 1660" in out.get("gpu", ""), True)
    check2("labeled not overwritten by RTX 4090", "4090" not in out.get("gpu", ""), True)

    # empty / no models
    out_empty = parse_description_text("")
    check2("empty", out_empty, {})
    out_none = parse_description_text("Parduodu kompiuteri be specifikaciju")
    check2("no models", out_none, {})

    # bare-sku fallback still works in prose ("rx vega 56" partly works;
    # bare "rtx 4090" is also caught)
    desc_bare = "Pardaviau GTX 1660 SUPER, veikia puikiai."
    out = parse_description_text(desc_bare)
    check2("bare gpu", "GTX 1660" in out.get("gpu", ""), True)

    print(f"\n{'PASS' if fails2 == 0 else f'{fails2} FAILURES'}")
    return 1 if (fails or fails2) else 0


if __name__ == "__main__":
    raise SystemExit(run())
