# Vendored TV-Anytime schemas

Unmodified copies of the three XSD files needed to validate a
`TVAMain` document, taken from the ETSI schema package for
**TS 102 822-3-1 v1.11.2 (2019-06)**, which is the version NorDig
profiles (NorDig TVA Implementation Guidelines 1.4, §2.3):

<https://www.etsi.org/deliver/etsi_ts/102800_102899/1028220301/01.11.02_60/ts_1028220301v011102p0.zip>

| File | Why it is here |
| --- | --- |
| `tva_metadata_3-1.xsd` | The prime schema. NorDig names this as the file to verify against. |
| `tva_mpeg7.xsd` | Imported by the above; supplies `mpeg7:` types such as `PersonNameType` and `MinimumAge`. |
| `xml.xsd` | Imported by the above; supplies `xml:lang`. |

They are vendored so that `agenda/tests/test_tvanytime.py` can assert the
feed validates on every run. Element order in TV-Anytime is a schema
constraint rather than a convention, so without this the only way to find
out that a field was added in the wrong place is for a distributor's
parser to reject the feed.

The other files in the ETSI package — the remaining schemas and the
classification-scheme XML — are not needed to validate and are not kept.

## Do not edit these

They are byte-identical to the published package and should be replaced
wholesale when tracking a new TV-Anytime version, not patched.

One known defect is worked around at load time rather than here:
`tva_metadata_3-1.xsd` writes `maxOccurs=" unbounded"` for
`ServiceInformationType/ServiceType`, with a leading space that no
XSD processor accepts. `test_tvanytime.tva_schema` repairs it in memory
and explains why. If a future ETSI release fixes it, that workaround
becomes a no-op and can go.
