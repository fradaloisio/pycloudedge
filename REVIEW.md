# Review di affidabilità — settembre 2026

Branch: `review/reliability-fixes`. Base: `acc63cc`.
Review coordinata con il repository `cloudedge-ha` sullo stesso nome di branch.

## Problemi corretti

| Priorità | Problema | Correzione |
| --- | --- | --- |
| P1 | La ricerca per nome sceglieva arbitrariamente il primo dispositivo in caso di nomi esatti o parziali ambigui. I comandi potevano raggiungere la camera sbagliata. | `DeviceNotFoundError` esplicito per corrispondenze ambigue; la corrispondenza esatta e univoca continua ad avere precedenza. Le entità HA usano direttamente seriale e device ID. |
| P1 | Il fallimento della richiesta a una casa veniva ignorato, restituendo un inventario parziale come se fosse completo. HA poteva rimuovere dispositivi dai dati e interromperne gli stream. | Propagazione dell'errore, preservando il segnale di autenticazione e impedendo la pubblicazione di inventari incompleti. |
| P2 | Cache con JSON valido ma struttura errata, timestamp non numerici/non finiti o credenziali mancanti potevano bloccare il login o essere accettate come sessioni valide. | Validazione della struttura, token, user ID e intervallo temporale; cache non utilizzabili ignorate. |
| P2 | Le cache legacy prive di endpoint regionali erano accettate senza inizializzare gli URL del client. | Discovery degli endpoint e aggiornamento della cache mantenendo il token valido, senza un nuovo login. |
| P2 | La cifratura della password importava TripleDES dal namespace deprecato di cryptography. | Import dal namespace `decrepit`, con fallback per le versioni precedenti. Output del protocollo invariato, verificato con un'implementazione crittografica indipendente. |

## Verifiche

**111 test superati** su Python 3.14, inclusi **20 nuovi casi** in `tests/test_reliability.py`. La suite comprende anche i test esistenti di streaming, KCP, MQTT, autenticazione e profili video.

```sh
PYTHONPATH=. venv/bin/python -m pytest tests -o addopts='' -q -p no:cacheprovider
```

I test iniziali hanno riprodotto i difetti di cache e discovery prima delle correzioni. L'integrazione associata ha superato 20 test nel container Home Assistant 2026.9.0; sono stati verificati anche caricamento, servizi, reload e ricezione di un segmento HLS reale. Il sorgente della libreria installata nel container è stato confrontato con quello del branch.

`git diff --check` e compilazione di tutti i moduli senza errori. Non sono state collaudate tutte le regioni, tutte le versioni Python dichiarate o la qualità audiovisiva. Per dettagli sull'istanza e sulle entità indisponibili, vedere `REVIEW.md` nel repository `cloudedge-ha`.

La ricerca ambigua ora genera un errore: chi usava nomi parziali deve renderli univoci oppure usare le API basate sul seriale. Nessun pacchetto è stato pubblicato e nessuna versione di release è stata incrementata.
