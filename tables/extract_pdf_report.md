# INEC PDF extraction report

House: 4273 candidate rows across 343/353 source PDFs.
Senate: 1853 candidate rows across 108/110 source PDFs.
12 issue(s).

These are not extraction failures -- each flagged file's own CODE prefix
(FC=federal constituency, SD=senatorial district, SC=state assembly) shows it
is genuinely the wrong office's data, mislabeled on INEC's own site:

- Kaduna North, Kaduna South: the only working URLs found for these (during
  the earlier broken-link fix) turn out to be Senate results, not House of
  Reps. The real House of Reps result for these two constituencies is not
  available anywhere on inecnigeria.org as far as we've found.
- Kaduna Kaura, Kaduna Kauru, Kogi Adavi: same story -- the working
  replacement URLs found earlier are actually State House of Assembly
  results, not House of Reps.
- Katsina Faskari/Kankara/Sabuwa, Kogi Bassa/Dekina: these downloaded fine
  from the original House of Reps index link, but the PDF itself is a State
  Assembly result mistakenly linked there by INEC.
- Oyo Central/North/South: these three are Senate senatorial district
  results mistakenly linked from the House of Reps index page. We already
  have proper Senate data for these three from senate_2019_pdfs directly.
- FCT Abaji-Gwagwalada, FCT Bwari Municipal: on the Senate index, these are
  sub-area collation sheets (not senatorial-district-level results) for
  FCT's single seat; the real FCT senatorial result is FCT_FCT-SD.pdf, which
  parsed cleanly and is already included.

## Issues
- Kaduna_KADUNA-NORTH.pdf: wrong office: code SD/052/KD looks like senate data, not house
- Kaduna_KADUNA-SOUTH.pdf: wrong office: code SD/054/KD looks like senate data, not house
- Kaduna_KAURA.pdf: wrong office: code SC/484/KD looks like state_assembly data, not house
- Kaduna_KAURU.pdf: wrong office: code SC/470/KD looks like state_assembly data, not house
- Katsina_FASKARI-KANKARA-SABUWA.pdf: wrong office: code SC/196/KT looks like state_assembly data, not house
- Kogi_ADAVI.pdf: wrong office: code SC/583/KG looks like state_assembly data, not house
- Kogi_BASSA-DEKINA.pdf: wrong office: code SC/606/KG looks like state_assembly data, not house
- Oyo_OYO-CENTRAL.pdf: could not confirm office (no FC/SD/SC code, title doesn't say Representatives)
- Oyo_OYO-NORTH.pdf: could not confirm office (no FC/SD/SC code, title doesn't say Representatives)
- Oyo_OYO-SOUTH.pdf: could not confirm office (no FC/SD/SC code, title doesn't say Representatives)
- FCT_ABAJI_GWAGWALADA.pdf: wrong office: code FC/359/FCT looks like house data, not senate
- FCT_BWARI-MUNICIPAL.pdf: wrong office: code FC/360/FCT looks like house data, not senate
