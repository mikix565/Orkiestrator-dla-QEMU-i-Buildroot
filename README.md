# Praca Inżynierska: profilowanie wariantów Buildroot w QEMU

Orkiestrator w Pythonie automatyzuje pomiary charakterystyki zasobowej systemów Linux dla systemów wbudowanych. Dla każdego wariantu konfiguracji Buildroota (x86_64) buduje obraz, uruchamia go wielokrotnie w QEMU z KVM i zbiera: wyniki Sysbench (CPU i pamięć), czas rozruchu oraz pamięć zajętą po starcie systemu.
## Wymagania

Host:

- Linux x86_64 z działającym KVM: prawo zapisu do /dev/kvm. Bez KVM można ustawić kvm = false w orchestrator/variants.toml, ale wyniki nie będą porównywalne z dołączonymi kampaniami.
- Python 3.14. Kampanię 20261027-212235 wykonano na 3.14.8, a kampanię 20261117-071832 i testy na 3.14.9. Inne wersje nie były sprawdzane.
- QEMU (qemu-system-x86_64 w PATH, inną binarkę wskazuje opcja --qemu). Dołączone kampanie: QEMU 11.1.2 (20261027-212235) i 11.1.3 (20261117-071832), wersje zapisane w meta.json.
- GNU Make oraz narzędzia wymagane przez Buildroota: lista w rozdziale "System requirements" instrukcji Buildroota 2025.02.
- Buildroot 2025.02.18. Nie jest częścią repozytorium (patrz Instalacja).
- Na hoście z biblioteką lz4 w wersji 1.11 budowa host-zstd w Buildroocie 2025.02.18 kończy się błędem kompilacji (nowsze lz4frame.h nie zawiera starej nazwy typu contentChecksum_t). Naprawia to łatka custom-layer/patches/zstd/0001-fileio-use-LZ4F_contentChecksum_t.patch, dołączana automatycznie przez BR2_GLOBAL_PATCH_DIR w custom-layer/factors/base.fragment.
- Wolny port TCP 1234 na 127.0.0.1 (zmiana: parametr port w variants.toml).
- Miejsce na dysku: ok. 8-9 GB na zbudowany wariant, czyli ok. 100 GB dla wszystkich 12. Maszyna wirtualna dostaje domyślnie 512 MB RAM i 2 procesory.

Pakiety Pythona (requirements.txt, wersje przypięte do użytych w pracy):

- pandas: wczytywanie i grupowanie runs.csv, obliczanie średnich.
- numpy: rozrzut punktów na wykresach pudełkowych (zależność pandasa, importowana też bezpośrednio).
- matplotlib: wykresy słupkowe i pudełkowe (PNG i SVG).
- pytest, pytest-cov: testy i pomiar pokrycia.

## Instalacja

Wszystkie polecenia z katalogu głównego repozytorium. Skrypt orch szuka interpretera w .venv w tym katalogu, więc środowisko musi nazywać się .venv.

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt

Orkiestrator oczekuje w katalogu projektu trzech katalogów: buildroot/, custom-layer/ i dl/. Katalog custom-layer/ jest w repozytorium. Katalog dl/ to cache pobieranych źródeł:

    mkdir -p dl

Buildroota pobierz ze strony https://buildroot.org/downloads/ (plik buildroot-2025.02.18.tar.xz) i rozpakuj tak, aby plik Makefile leżał w buildroot/Makefile. Jeśli chcesz tylko analizować dołączone wyniki bez budowania, wystarczy pusty katalog:

    mkdir -p buildroot

Sprawdzenie instalacji (testy z atrapami, bez QEMU i Buildroota):

    cd orchestrator && ../.venv/bin/python -m pytest -q 2>&1 | tail -n 3

## Szybki start: analiza dołączonych wyników

Repozytorium zawiera surowe dane dwóch kampanii, każda z 12 wariantami po 10 powtórzeń (120 przebiegów pomiarowych i 12 rozgrzewkowych):

- 20261117-071832: kampania główna, której wyniki opisano w pracy; wszystkie warianty zbudowano przed nią od zera (czasy budowy w orchestrator/results/builds.csv),
- 20261027-212235: wcześniejsza kampania z tym samym zestawem wariantów i scenariuszy, użyta do porównania powtarzalności wyników.

Po instalacji:

    ./orch analyze --experiment 20261117-071832

Polecenie wypisuje tabelę średnich i zapisuje w orchestrator/results/20261117-071832/analysis/ pliki averages.csv, stats.csv, averages.png, averages.svg, boxplots.png i boxplots.svg. Plik stats.csv ma wiersz dla każdej pary wariant i metryka, z kolumnami n, mean, std (odchylenie standardowe z próby), median, q1, q3, min i max. Bez opcji --experiment analizowany jest najnowszy eksperyment w katalogu wyników.

## Użycie

Skrypt orch uruchamia orkiestrator z właściwego katalogu i interpretera, więc można go wywołać z dowolnego miejsca. Polecenia:

    ./orch check
    ./orch build
    ./orch run
    ./orch analyze
    ./orch all

Opcje ogólne, przed nazwą polecenia: --project-dir (katalog projektu, domyślnie wykrywany względem położenia kodu) i --config (plik konfiguracji). Ścieżki względne w tych opcjach są liczone od katalogu orchestrator/, więc podawaj ścieżki bezwzględne.

- check: wypisuje czynniki z poziomami, warianty ze stanem budowy, scenariusze i ustawienia wykresów oraz sprawdza konfigurację bez budowania: składa defconfig każdego wariantu (powtórzona opcja to błąd), porównuje zbudowane out/NAZWA/.config z fragmentami czynników i ostrzega o brakujących kombinacjach w siatce. Przy błędzie kończy się kodem 1.
- build: konfiguruje i buduje obrazy. Opcje: --variant NAZWA (można powtarzać, domyślnie wszystkie warianty) i --clean (czysta budowa). Czysta budowa trwała 20-25 minut na wariant na moim sprzęcie, przyrostowa kilkanaście sekund (odświeża overlay). Czasy i rozmiary trafiają do orchestrator/results/builds.csv, a czas budowy ma sens tylko dla budów z --clean. Zmiana opcji w już zbudowanym pakiecie bez --clean bywa pomijana przez Buildroota.
- run: uruchamia eksperyment na zbudowanych obrazach. Opcje: --variant, --reps N (powtórzenia), --warmup N (rozgrzewka), --qemu BIN.
- analyze: tabela i wykresy średnich oraz pudełkowe. Opcja --experiment ID.
- all: build, run i analyze jednym poleceniem.

Przykład krótkiego przebiegu dwóch wariantów (obrazy muszą być zbudowane):

    ./orch run --variant base --variant base_tiny --reps 1 --warmup 1
    ./orch analyze

Rekomendacja, nie wymóg: czysta budowa wszystkich wariantów trwa kilka godzin, więc warto uruchomić ją w tmux lub screen, żeby zerwanie sesji jej nie przerwało.

## Jak działa pomiar

1. Konfiguracja wariantu: czynnikami są podkatalogi custom-layer/factors/, a poziomami pliki POZIOM.fragment w tych podkatalogach. Defconfig Buildroota składa się z custom-layer/factors/base.fragment i fragmentu wybranego poziomu każdego czynnika. Zduplikowana opcja w dwóch fragmentach to błąd. Wygenerowane defconfigi trafiają do out/_defconfigs/, a katalogi wyjściowe do out/NAZWA/. Przed pomiarem program sprawdza, czy każda opcja z fragmentów wariantu występuje z tą samą wartością w out/NAZWA/.config.
2. Pojedynczy przebieg: sonda obciążenia hosta przez 1 s (z /proc/stat), potem host otwiera nasłuch TCP na 127.0.0.1 i uruchamia QEMU (-kernel bzImage, dysk rootfs.ext2 przez virtio z snapshot=on, konsola szeregowa, KVM). Parametry dla gościa (liczba wątków, port, scenariusze) idą w linii poleceń jądra.
3. Gość: skrypt S99benchmark (custom-layer/board/overlay/etc/init.d/) czyta /proc/cmdline, wypisuje na konsolę BOOT_DONE, zapisuje MemTotal i MemAvailable, uruchamia scenariusze Sysbench i wysyła raport przez nc na 10.0.2.2 (host widziany z sieci użytkownika QEMU), po czym wyłącza system.
4. Dwa zegary rozruchu: boot_host_s (host: od startu procesu QEMU do linii BOOT_DONE na konsoli) i boot_uptime_s (gość: /proc/uptime w skrypcie startowym). Oba kończą się w skrypcie startowym, a nie w gotowości usług.
5. Plan kampanii: rozgrzewka (po jednym przebiegu każdego wariantu, poza analizą), potem bloki pomiarowe, w których każdy wariant występuje raz w losowej kolejności (ziarno seed w variants.toml, domyślnie 42).
6. Statusy przebiegu: ok, timeout, no_boot, no_report, truncated_report, qemu_failed, parse_error (szczegóły w kolumnie note). Przebieg przerwany po timeout_s (domyślnie 120 s).
7. Analiza: bierze tylko przebiegi pomiarowe ze statusem ok i pomija te, w których przed startem host był zajęty ponad max_idle_busy_pct (domyślnie 10%). Pominięte przebiegi zostają w runs.csv. Zajęta pamięć to MemTotal minus MemAvailable po starcie.

## Warianty i scenariusze

Siatka 2x3x2 = 12 wariantów: libc (glibc, musl), optymalizacja (O2, O3, Os), jądro (default: board/qemu/x86_64/linux.config z Buildroota, tiny: custom-layer/board/linux-tiny.defconfig, jądro 6.12.27). Nazwy: base, musl, os, o3, musl_os, musl_o3 i odpowiedniki z końcówką _tiny.

Scenariusze Sysbench (testy cpu i memory): cpu (2 wątki, cpu-max-prime=20000), mem (zapis, blok 64M, 10 s), mem_read (odczyt) i cpu_1t (1 wątek).

Dodawanie wariantu: wpis [[variant]] w orchestrator/variants.toml z nazwą i poziomem każdego czynnika (kolejność czynników w kolumnach wyników i etykietach wynika z kolejności kluczy w pierwszym wariancie). Nowy poziom czynnika to nowy plik custom-layer/factors/CZYNNIK/POZIOM.fragment. Nowy czynnik to nowy podkatalog custom-layer/factors/CZYNNIK/ z fragmentami poziomów; po jego dodaniu każdy wariant musi podać poziom tego czynnika. Po zmianach warto uruchomić ./orch check. Dodawanie scenariusza: wpis [[scenario]] (przykłady w variants.toml). Opcje Sysbencha pochodzą z białej listy, scenariusze są kodowane w linii poleceń jądra (limit 1200 znaków, wartości tylko z znaków A-Za-z0-9._), a nazwa scenariusza staje się przedrostkiem kolumn w runs.csv.

## Wykresy

Wygląd wykresów ustawia opcjonalna sekcja [plot] w orchestrator/variants.toml:

- columns, rows: układ paneli (jeden panel na scenariusz lub wielkość systemową). Bez nich układ dobierany jest automatycznie, np. 7 paneli to 3x3, 16 paneli to 4x4.
- color_by, colors: czynnik, którego poziomy wyznaczają kolor, i opcjonalne kolory poziomów (nazwy lub kody matplotlib). Domyślnie kolor według pierwszego czynnika.
- hatch_by, hatches: czynnik, którego poziomy wyznaczają kreskowanie, i opcjonalne wzory poziomów. Domyślnie bez kreskowania.
- order, sort_by: kolejność poziomów wybranych czynników i kolejność czynników przy sortowaniu wariantów na osi.

Dołączony variants.toml ustawia 4 kolumny, kolor według libc, kreskowanie jądra tiny oraz sortowanie według jądra, biblioteki i poziomu optymalizacji (O2, O3, Os).

## Wyniki

Każdy eksperyment tworzy katalog orchestrator/results/RRRRMMDD-GGMMSS/ z plikami:

- runs.csv: wiersz na przebieg, dopisywany od razu z fsync (przerwana kampania nie gubi zapisanych wierszy). Kolumny: identyfikatory, czynniki, faza, status, boot_host_s, boot_uptime_s, pamięć gościa, sonda obciążenia hosta i metryki scenariuszy.
- meta.json: wersje narzędzi, dane hosta, czynniki i ich poziomy w wariantach, scenariusze i plan kampanii.
- raw/: surowe raporty i logi konsoli.
- analysis/: wyniki polecenia analyze.

## Testy

261 testów z atrapami zamiast QEMU (test_guest.py uruchamia prawdziwy S99benchmark z atrapą Sysbencha). Pokrycie kodu mierzy się z katalogu orchestrator:

    cd orchestrator && ../.venv/bin/python -m pytest -q --cov=orchestrator --cov-report=term-missing

Pokrycie całego pakietu wyniosło ok. 90% przy pierwszym pomiarze. Najniższe ma qemu_runner.py (31%), bo testy nie uruchamiają prawdziwego QEMU. Ta część jest weryfikowana kampaniami na prawdziwych obrazach.

## Struktura repozytorium

- orch: skrypt uruchamiający.
- orchestrator/orchestrator/: kod (cli, config, variants, scenarios, builder, qemu_runner, parser, storage, hoststate, experiment, analysis, check).
- orchestrator/variants.toml: konfiguracja eksperymentu, wariantów i scenariuszy.
- orchestrator/tests/: testy (tests/data to próbki raportów).
- orchestrator/results/: wyniki (dwie kampanie i builds.csv).
- custom-layer/: BR2_EXTERNAL (pakiety sysbench 1.0.20 i ck 0.7.2, fragmenty konfiguracji, overlay ze skryptem gościa).
- requirements.txt: zależności Pythona.

Katalogi buildroot/, dl/, out/ i .venv/ powstają lokalnie i nie są w repozytorium.

## Ograniczenia

- Wyniki są względne i pochodzą z jednego hosta (Intel i5-8600K, 16 GB RAM, Linux 6.18, governor powersave). Częstotliwość procesora nie była kontrolowana, a przypinanie do rdzeni (cpu_affinity) jest domyślnie wyłączone.
- Przed kampanią zamknij programy w tle. Na zajętym hoście zakłócenia sięgały 3-9% w CPU i pamięci, a kampanie końcowe wykonano przy hoście bezczynnym w ponad 98%.
- Czas rozruchu kończy się w skrypcie startowym, nie w pełnej gotowości usług i nie obejmuje firmware.
- Mierzone są tylko Sysbench CPU i pamięć oraz pamięć zajęta po starcie. Zużycie zasobów w trakcie testu nie jest próbkowane.
- Czas czystej budowy ma po jednym pomiarze na wariant (budowy z 2026-11-17 przed kampanią 20261117-071832). W builds.csv są też starsze budowy oraz nieudana budowa base sprzed dodania łatki zstd, które należy pominąć przy porównaniach.
- Między kampaniami zaktualizowano system hosta: jądro 6.18.58 do 6.18.60, QEMU 11.1.2 do 11.1.3, Python 3.14.8 do 3.14.9 oraz lz4 do wersji 1.11, która wymusiła łatkę zstd.
