# Mesh yakınsama çalışması

Aynı geometri, yasak hacimler, uç noktalar ve parametrelerle mesh her iki yönde 1,5 ve 2 kat sıklaştırılır; baseline ve bütünleşik yöntem yeniden çalıştırılır ve bağımsız denetimlerle ölçülür. "e) / bükülme alt sınırı": e) toplam uzunluğunun, bükülme kısıtlı en kısa yollar toplamına oranı.

| Mesh | Ayrıt aralığı [m] | Düğüm | a) uzunluk [m] | a) EMC [nokta] | e) uzunluk [m] | e) / bükülme alt sınırı | e) EMC / kapasite / bükülme / yasak / boşluk | e) süre [s] |
|:---|---:|---:|---:|---:|---:|---:|:---:|---:|
| 1× | 0.100 | 3475 | 61.66 | 987 | 66.05 | 1.071 | 0 / 0 / 0 / 0 / 0 | 0.11 |
| 1.5× | 0.067 | 7694 | 61.22 | 734 | 65.80 | 1.074 | 0 / 0 / 0 / 0 / 0 | 0.19 |
| 2× | 0.050 | 13622 | 61.29 | 742 | 85.89 | 1.370 | 0 / 0 / 0 / 0 / 0 | 4.53 |
