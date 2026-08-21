"""Controlled-term references used by the TV-Anytime feed.

Named here rather than spelled out at each use so that the version year
baked into every URI -- `:2012:`, `:2019:`, `:2022:` -- is visible in one
place when a scheme is revised. The `urn:nordig:` schemes are NorDig's
own additions, which is why they are not `urn:tva:`: NorDig moved them
out of the TVA namespace in v1.3 precisely because they are not part of
the ETSI specification.
"""

# --- ETSI TV-Anytime schemes (ts_1028220301v011102p0.zip) ---------------

# HowRelatedCS:2012, for what a piece of RelatedMaterial *is*.
HOW_RELATED_PROGRAMME_WEBSITE = "urn:tva:metadata:cs:HowRelatedCS:2012:10.2"

# mpeg7 RoleCS:2011, for CreditsItem/@role.
ROLE_PRODUCER = "urn:mpeg:mpeg7:cs:RoleCS:2011:PRODUCER"

# --- NorDig schemes (NorDigTVAGuidelines_1.4_June_2022.zip) -------------

# ServiceTypeCS:2019. A service carries one term for its delivery and one
# for its medium, so a linear TV channel is both `linear` and `video`.
SERVICE_TYPE_LINEAR = "urn:nordig:metadata:cs:ServiceTypeCS:2019:linear"
SERVICE_TYPE_ON_DEMAND = "urn:nordig:metadata:cs:ServiceTypeCS:2019:onDemand"
SERVICE_TYPE_VIDEO = "urn:nordig:metadata:cs:ServiceTypeCS:2019:video"

# HowRelatedNordigCS:2022, which subdivides HowRelatedCS term 19
# ("Promotional Still Image") into the image roles broadcasters actually
# distinguish -- key art, episode still, portrait, channel logo, and more.
#
# Only one of them appears here, because only one describes an image we
# hold: 19.4 is the generic representative image for a programme, which is
# what ingest produces, one frame chosen to stand for the whole video at
# three sizes. The rest of the scheme waits on there being editorial
# imagery to point at; see docs/tvanytime.md.
HOW_RELATED_SHOW_STILL = "urn:nordig:metadata:cs:HowRelatedNordigCS:2022:19.4"
