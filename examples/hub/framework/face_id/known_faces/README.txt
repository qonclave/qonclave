Put your reference face images here.

File name = person's display name (spaces become underscores).

Examples:
  alice.jpg         -> identified as "alice"
  bob_smith.jpg     -> identified as "bob_smith"
  john_doe.png      -> identified as "john_doe"

MORE THAN ONE PHOTO PER PERSON
------------------------------
A single frontal photo matches poorly once someone turns away or slumps --
which is exactly when fall detection needs to keep recognizing them. Enroll
several angles for one person using either layout:

  jogendra.jpg        }
  jogendra__2.jpg     }  all three are the person "jogendra"
  jogendra__3.jpg     }

  jogendra/front.jpg  }
  jogendra/side.jpg   }  also all "jogendra" (one directory per person)

Recognition takes the BEST match across a person's photos, not the average,
so adding an angle can only help -- an averaged frontal+profile template
would match neither pose well.

Note the DOUBLE underscore. A single one is part of the name:
  jogendra_1.jpg    -> a DIFFERENT person called "jogendra_1"
That distinction matters: posture timers and follow priorities are keyed by
identity, so a second file read as a second person resets a collapsing
person's timers every time the match flips between the two.

From the dashboard, tick "additional photo" when enrolling (or POST
/user/known_faces with additional=1) and the hub files it as slug__N for you.

Supported formats: jpg, jpeg, png, webp
