from django.db import migrations, models

# Django sets created_time (auto_now_add) and updated_time (auto_now) on
# every save, so nothing written through the ORM has ever been NULL. The
# nullability only ever covered rows imported around Django, of which
# production had 141 -- all with an uploaded_time or an updated_time to
# date them by. VideoFile.created_time was nullable on the same grounds
# and had no NULLs at all.
#
# Production was backfilled by hand before this migration was written,
# so this runs against nothing there. It is here for every other copy of
# the database: dev machines, staging, and any restore of an older dump,
# where the AlterField below would otherwise fail on rows the cleanup
# never touched.
# 
# The following videos were backfilled with the uploaded_time or updated_time
# of the original video file:
#
# fkweb=# select id, name from fk_video where created_time is null;
#    id   |                                               name
# --------+---------------------------------------------------------------------------------------------------
#  625529 | TED - Linus Torvalds: The mind behind Linux
#  625527 | Practical Protocol-Oriented Programming in Swift
#  625523 | TED - Tania Simoncelli: How I took on the gene patent industry -- and won
#  625519 | TED - Aomawa Shields: How we'll find life on other planets
#  625516 | TED - Paul Greenberg: The four fish we're overeating -- and what to eat instead
#  625512 | TED - David Rothkopf: How fear drives American politics
#  625504 | TED - Lucianne Walkowicz: Let's not use Mars as a backup planet
#  625426 | MakerCon - Autodesk’s 3D Printing Platform: Sparking innovation
#  625425 | MakerCon - How Making is Changing the World – and How You Can Too!
#  625501 | Random Aussies - Outtakes 1
#  625420 | MakerCon - The Impact of the Maker Movement
#  625424 | MakerCon - Open-Source Adventuring: OpenROV, OpenExplorer and the Future of Connected Exploration
#  625496 | Random Aussies - Meningie
#  625485 | TED - Andreas Ekström: The moral bias behind your search results
#  625484 | TED - Johann Hari: Everything you think you know about addiction is wrong
#  625507 | TED - Daniel Kish: How I use sonar to navigate the world
#  625482 | TED - James Veitch: This is what happens when you reply to spam email
#  625481 | TED - Nancy Lublin: Texting that saves lives
#  625478 | TED - Melissa Fleming: A boat carrying 500 refugees sunk at sea. The story of two survivors
#  625475 | TED - Harald Haas: Wireless data from every light bulb
#  625472 | TED - Mathias Jud: Art that lets you talk back to NSA spies
#  625470 | TED - Vijay Kumar: The future of flying robots
#  625465 | TED - Michael Green: How we can make the world a better place by 2030
#  625463 | TED - Laura Schulz: The surprisingly logical minds of babies
#  625461 | TED - Robin Murphy: These robots come to the rescue after a disaster
#  625385 | Piwik - Webstatistikk med fri programvare
#  625459 | TED - Christopher Soghoian: A brief history of phone wiretapping -- and how to avoid it
#  625458 | Kommunenes digitale arkiv som et felleskomponent basert på fri programvare
#  625457 | TED - Wendy Freedman: This new telescope might show us the beginning of the universe
#  625455 | TED - John Green: The nerd's guide to learning everything online
#  625451 | TED - Dame Ellen MacArthur: The surprising thing I learned sailing solo around the world
#  625447 | TED - Donald Hoffman: Do we see reality as it is?
#  625423 | MakerCon - Making in Norway
#  625419 | TED - Trevor Aaronson: How this FBI strategy is actually creating US-based terrorists
#  625446 | Civic Tech and the Sad Story of Freedom of Information in Hungary
#  625445 | TED - Rajiv Maheswaran: The math behind basketball's wildest moves
#  625440 | TED - Chris Urmson: How a driverless car sees the road
#  625437 | MakerCon - Evolutionary algorithms as a design tool - from art to robotics
#  625436 | MakerCon - Make and break
#  625435 | MakerCon - Making a one year school course for young makers
#  625434 | MakerCon - Innovation Inspiration - IPR Databases as a Source of Inspiration
#  625433 | MakerCon - Making a toy for makers
#  625432 | MakerCon - How to make 3D printer electronics
#  625431 | MakerCon - Hovering Clouds: Looking at online tool offerings for Product Design and 3D Printing
#  625430 | MakerCon - Travelling maker stories
#  625429 | MakerCon - Making the first Maker Faire in Sweden
#  625428 | MakerCon - Breaking the mold: Printing 1000’s of parts
#  625427 | MakerCon - Ultimaker — and open source 3D printing
#  625386 | TED - Anand Varma: A thrilling look at the first 21 days of a bee’s life
#  625384 | Open Source in Broadcasting
#  625383 | TED - Dan Ariely: How equal do we want the world to be? You'd be surprised
#  625382 | Erfaringer fra drift av sks-keyservers.net
#  625381 | TED - Alice Goffman: How we're priming some kids for college — and others for prison
#  625380 | Teknologien bak den digitale valutaen Bitcoin
#  625379 | TED - Abe Davis: New video technology that reveals an object's hidden properties
#  625378 | Fri programvareløsning for NOARK 5-kjerne og dots i praksis
#  625377 | TED - Greg Gage: How to control someone else's arm with your brain
#  625376 | Console Password Manager - Passord-behandling i terminalen
#  625374 | TED - Chris Milk: How virtual reality can create the ultimate empathy machine
#  625373 | OpenStack og drift i nettskyen
#  625372 | TED - Nathalie Cabrol: How Mars might hold the secret to the origin of life
#  625370 | TED - Gary Haugen: The hidden reason for poverty the world needs to address now
#  625368 | Citizenfour - Samtale med Snowden-advokat Patrick Toomey og Jon Wessel-Aas før visning
#  625367 | Killswitch-debatt med A. Brenna, H.W. Lie, K. Miazine og P. Reinholdtsen
#  625365 | TED - Fei-Fei Li: How we're teaching computers to understand pictures
#  625363 | TED - Andy Yen: Think your email's private? Think again
#  625361 | TED - Topher White: What can save the rainforest? Your used cell phone
#  625359 | TED - Ben Wellington: How we found the worst place to park in New York City -- using big data
#  625357 | TED - Dan Dennett on our consciousness
#  625356 | TED - Michael Green: What the Social Progress Index can reveal about your country
#  625354 | TED - Sanjay Pradhan: How open data is changing international aid
#  625353 | TED - Sergei Lupashin: A flying camera ... on a leash
#  625352 | TED - Apollo Robbins: The art of misdirection
#  625351 | TED - Daniel Suarez: The kill decision shouldn't belong to a robot
#  625350 | TED - Kirby Ferguson: Embrace the remix
#  625349 | TED - Ernesto Sirolli: Want to help someone? Shut up and listen!
#  625348 | TED - James Randi's fiery takedown of psychic fraud
#  625347 | TED - Michael Specter: The danger of science denial
#  625346 | TED - Dave deBronkart: Meet e-Patient Dave
#  625345 | TED - Rob Reid: The $8 billion iPod
#  625344 | TED - William Noel: Revealing the lost codex of Archimedes
#  625343 | TED - Marco Tempest: Maybe the best robot demo ever
#  625342 | TED - Marco Tempest: A cyber-magic card trick like no other
#  625341 | TED - Marco Tempest: Augmented reality, techno-magic
#  625339 | TED - Marco Tempest: A magical tale (with augmented reality)
#  625338 | TED - Marco Tempest: The electric rise and fall of Nikola Tesla
#  625337 | TED - Mitch Resnick: Let's teach kids to code
#  625336 | TED - Geoffrey Canada: Our failing schools. Enough is enough!
#  625334 | TED - Sir Ken Robinson: Bring on the learning revolution!
#  625333 | TED - Peter van Manen: How can Formula 1 racing help ... babies?
#  625332 | TED - Britta Riley: A garden in my apartment
#  625331 | TED - Ron Finley: A guerilla gardener in South Central LA
#  625330 | TED - David Gallo shows underwater astonishments
#  625329 | TED - Bruno Torturra: Got a smartphone? Start broadcasting
#  625328 | TED - Tom Wujec: Got a wicked problem? First, tell me how you make toast
#  625325 | TED - Lawrence Lessig: We the People, and the Republic we must reclaim
#  625324 | TED - Laura Snyder: The Philosophical Breakfast Club
#  625323 | TED - Keith Chen: Could your language affect your ability to save money?
#  625322 | TED - James B. Glattfelder: Who controls the world?
#  625321 | TED - Heather Brooke: My battle to expose government corruption
#  625320 | TED - Erik Schlangen: A "self-healing" asphalt
#  625319 | TED - Eric Dishman: Health care should be a team sport
#  625318 | TED - David Pizarro: The strange politics of disgust
#  625317 | TED - Bruce Feiler: Agile programming -- for your family
#  625316 | TED - Afra Raymond: Three myths about corruption
#  625315 | TED - Brian Greene: Is our universe the only universe?
#  625309 | TED - Leah Buechley: How to “sketch” with electronics
#  524870 | tekstplakat - andre - foreningen frikanalen
#  624097 | Trailer Fen Paulus religion
#  625335 | TED - Ken Robinson: How to escape education's death valley
#  524818 | tekstplakat - idrett - frikanalen_tv
#  524753 | tekstplakat - kultur -  programkategori
#  525091 | ESTV uke 04
#  524874 | tekstplakat - andre - vignett og åpen for alle
#  524813 | tekstplakat - idrett - vignett og aapen for alle
#  524780 | tekstplakat - åpen - programkategori
#  524779 | tekstplakat - åpen - frikanalen_tv
#  524778 | tekstplakat - åpen - frikanalen_tv
#  524776 | tekstplakat - åpen - foreningen frikanalen
#  524775 | tekstplakat - vignett og åpen for alle
#  524774 | tekstplakat - solidaritet - vignett og åpen for alle
#  524772 | tekstplakat - solidaritet - programkategori
#  524771 | tekstplakat - solidaritet - frikanalen_tv
#  625502 | Random Aussies - Outtakes 2
#  625500 | Random Aussies - Season highlights
#  625499 | Random Aussies - Greelong
#  625498 | Random Aussies - McClaren Vale
#  625495 | Random Aussies - Hamilton
#  625494 | Random Aussies - Mooloolaba
#  625493 | Random Aussies - Bundaberg
#  625492 | Random Aussies - Stanthorpe
#  625491 | Random Aussies - Teamworth
#  625490 | Random Aussies - Griffith
#  625488 | Random Aussies - Batemans Bay
#  625497 | Random Aussies - Victor Harbor
#  524814 | tekstplakat - idrett -programkategori
#  524872 | tekstplakat - andre - programkategori
#  524749 | tekstplakat - religion-programkategori
#  625449 | TED - Tal Danino: We can use bacteria to detect cancer (and maybe treat it)
#  625438 | TED - Maryn McKenna: What do we do when antibiotics don’t work any more?
#  625340 | TED - Marco Tempest: The magic of truth and lies (and iPods)
#
# A sequence rather than one string: Django runs each entry as its own
# statement, where a multi-statement string would depend on sqlparse
# being installed to split it.
BACKFILL = (
    """
    UPDATE fk_video
    SET created_time = COALESCE(uploaded_time, updated_time)
    WHERE created_time IS NULL
    """,
    """
    UPDATE fk_video
    SET updated_time = COALESCE(created_time, uploaded_time)
    WHERE updated_time IS NULL
    """,
    # Runs after the two above, so the video it reads from is itself
    # already dated. The FK is NOT NULL, so this cannot leave a NULL
    # behind.
    """
    UPDATE fk_videofile
    SET created_time = (
        SELECT created_time FROM fk_video WHERE fk_video.id = fk_videofile.video_id
    )
    WHERE created_time IS NULL
    """,
)


class Migration(migrations.Migration):
    dependencies = [
        ("fk", "0019_scheduleitem_weekly_slot_provenance"),
    ]

    operations = [
        migrations.RunSQL(
            sql=BACKFILL,
            # Nothing to undo: the rows carried no creation time to
            # restore, and reversing the AlterField makes NULL legal
            # again rather than required.
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name="video",
            name="created_time",
            field=models.DateTimeField(
                auto_now_add=True, help_text="Time the program record was created"
            ),
        ),
        migrations.AlterField(
            model_name="video",
            name="updated_time",
            field=models.DateTimeField(
                auto_now=True, help_text="Time the program record has been updated"
            ),
        ),
        migrations.AlterField(
            model_name="videofile",
            name="created_time",
            field=models.DateTimeField(
                auto_now_add=True, help_text="Time the video file was created"
            ),
        ),
    ]
