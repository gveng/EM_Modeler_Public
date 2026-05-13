# EM 3D Modeler Help

Versione: 1.0.0
Data rilascio: 2026-05-13

## 1. Panoramica
EM 3D Modeler e un ambiente CAD/EM 3D per creare geometrie, assegnare materiali, importare STEP, eseguire operazioni booleane e generare script EMERGE.

## 2. Interfaccia
- Pannello sinistro: Project Tree e proprieta dell'oggetto selezionato.
- Centro: viewport 3D e barra informazioni.
- Pannello destro: Objects/Materials, selezione multipla e azioni rapide.
- Toolbar: primitive, sketch, booleane, import STEP, griglia, workspace, unita, modalita di selezione.

## 3. Creazione Geometrie
- Box, Cylinder, Cone, Sphere: cliccare sull'icona e disegnare nel viewport.
- Sketch: apre il canvas parametrico per estrusione o rivoluzione.

Suggerimento:
- Impostare prima Plane e Grid per un disegno piu preciso.

## 4. Selezione e Modifica
- Selezione singola: clic su oggetto nel viewport o nel tree.
- Selezione multipla: usare Ctrl/Shift nel tree materiali.
- Delete Selected: elimina l'oggetto selezionato.
- Body Properties: modifica parametri geometrici, materiale, colore e opacita.

## 5. Materiali
- Il pannello Objects/Materials raggruppa gli oggetti per materiale.
- E possibile applicare materiali in bulk su piu oggetti selezionati.
- Database materiali supportati:
  - Built-in
  - Project DB
  - Global DB
- Menu File:
  - Set Global Material DB
  - Reload Global Material DB

## 6. Operazioni Booleane
- Cut: sottrae i Tool dal Base.
- Fuse: unisce piu oggetti.
- Common: mantiene solo l'intersezione.

Flusso consigliato:
1. Selezionare Base + Tool (o piu Tool).
2. Confermare la finestra di riepilogo.
3. Il risultato sostituisce gli oggetti originali.

Nota su STEP complessi:
- Per mesh importate non-manifold o disgiunte, il sistema usa fallback robusti per evitare crash.

## 7. Import STEP
- File -> Import STEP o bottone Import STEP in toolbar.
- Sono supportati .step e .stp.
- Ogni solido importato viene creato come MeshObject separato.

## 8. Piani di Riferimento
- View -> Set Reference Plane per creare piani custom.
- Il piano attivo orienta griglia e strumenti di disegno.
- Dal tree materiali e possibile rinominare, attivare o rimuovere piani.

## 9. Salvataggio e Export
- Save Project / Save Project As: salva il progetto in formato .em3d.
- Export EMERGE Script: genera uno script .em compatibile con EMERGE.

## 10. Vista e Navigazione
- View menu:
  - Reset Camera
  - Top (XY)
  - Front (XZ)
  - Right (YZ)
  - Isometric

## 11. Risoluzione Problemi
- Boolean fallita:
  - Verificare che gli oggetti siano validi e con geometria non vuota.
  - Con STEP complessi, usare selezioni piu piccole e progressive.
- Import STEP con errori:
  - Controllare integrita del file CAD.
  - Provare a riesportare lo STEP dal CAD originale.
- Materiale non visibile nel progetto:
  - Ricaricare il Global DB e verificare i nomi duplicati.

## 12. Comandi Rapidi
- New Project: Ctrl+N
- Open Project: Ctrl+O
- Save Project: Ctrl+S
- Quit: shortcut di sistema (es. Alt+F4 su Windows)

## 13. About
Nel menu Help -> About trovi:
- Nome programma
- Versione
- Data rilascio
- Data corrente
