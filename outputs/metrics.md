# Metrikler

| Yöntem | Toplam uzunluk [m] | Benzersiz uzunluk [m] | Demetlenme oranı | Kablo kütlesi [kg] | EMC ihlali [nokta] | Bükülme ihlali | Yasak hacim ihlali | Boşluk payı ihlali [nokta] | Kapasite ihlali [ayrıt] | Süre [s] |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| a) Baseline (bağımsız Dijkstra) | 61.66 | 51.65 | 0.162 | 10.84 | 987 | 0 | 0 | 0 | 20 | 0.01 |
| b) Demetleme | 71.84 | 17.80 | 0.752 | 12.37 | 5661 | 15 | 0 | 0 | 116 | 0.03 |
| c) EMC duyarlı demetleme | 66.18 | 35.22 | 0.468 | 11.39 | 0 | 8 | 0 | 0 | 35 | 0.08 |
| d) Lagrange gevşetmesi (K=2) | 61.76 | 53.80 | 0.129 | 10.85 | 895 | 0 | 0 | 0 | 0 | 2.44 |
| e) Bütünleşik (EMC + kapasite + bükülme) | 66.05 | 40.80 | 0.382 | 11.32 | 0 | 0 | 0 | 0 | 0 | 0.13 |
| f) Dallı demet (Steiner ağacı) | 66.50 | 40.47 | 0.391 | 11.48 | 0 | 0 | 0 | 0 | 0 | 0.56 |

- Kapasite K = 2 kablo/ayrıt, minimum bükülme yarıçapı = 0.1 m, yasak hacim güvenlik payı = 0.05 m; EMC, yasak hacim ve boşluk denetimleri 0.05 m örnekleme ile (EMC mesafesi diğer rotanın doğru parçalarına kesin olarak ölçülür).
- Kablo kütlesi: sınıf başına kg/m × rota uzunluğu (power 0.3 kg/m, signal 0.12 kg/m, data 0.08 kg/m); demet bağları ve kelepçeler dahil değildir.
- Lagrange: 60 iterasyon, alt sınır 61.7624 m, üst sınır 61.7639 m, fark 0.0014 m (%0.002).
- e) için uzunluk alt sınırı 61.76 m (Lagrange alt sınırı; bükülme sınırı 61.66 m, Lagrange 61.76 m): e) toplam uzunluğu 66.05 m, en kısa uygun çözüme en çok %6.5 uzak.
- Güvenlik payı olmadan kurulan çizgede baseline 69 boşluk payı ihlali noktası üretir (boşluk denetiminin çalıştığını gösterir).
