# Kesin çözümle karşılaştırma

Örnek: 0.20 m mesh (912 düğüm, 5092 yönlü ayrıt, 26968 izinli dönüş), kablolar S4, D2, K = 2. Amaç: toplam uzunluk; kısıtlar: bükülme (dönüş çizgesi), kapasite, EMC ayrımı. MILP: 53960 ikili değişken, 39872 kısıt, HiGHS (scipy.optimize.milp), süre sınırı 120 s.

| Çözüm | Toplam uzunluk [m] | Denetimler (EMC / kapasite / bükülme / yasak / boşluk) | Süre [s] |
|:---|---:|:---:|---:|
| e) Bütünleşik | 12.456 | 0 / 0 / 0 / 0 / 0 | 0.01 |
| MILP en iyi çözüm (süre sınırına ulaşıldı) | 11.854 | 0 / 0 / 0 / 0 / 0 | 120.8 |

- Kanıtlanmış alt sınır: 11.472 m. e), optimuma en çok %7.9 uzaktır; MILP'in bulduğu en iyi çözümden %4.8 uzundur.
