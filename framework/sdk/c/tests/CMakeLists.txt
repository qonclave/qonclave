#
# Tests for the C binding.
#
# test_placement covers real logic and must pass. qc_conformance is wired but reports skipped
# until the codec exists — see sdk/c/README.md.
#

add_executable(qc_test_placement test_placement.c)
target_link_libraries(qc_test_placement PRIVATE qonclave)
add_test(NAME placement COMMAND qc_test_placement)

add_executable(qc_test_codec test_codec.c)
target_link_libraries(qc_test_codec PRIVATE qonclave)
add_test(NAME codec COMMAND qc_test_codec)

add_executable(qc_conformance qc_conformance.c)
target_link_libraries(qc_conformance PRIVATE qonclave)
# The emit directory receives c-uplink-minimal.cbor, which Python's test_interop.py decodes and
# compares against the JSON fixture. That comparison is the real cross-language assertion.
add_test(NAME conformance
         COMMAND qc_conformance
                 ${CMAKE_CURRENT_SOURCE_DIR}/../../../conformance/cases
                 ${CMAKE_CURRENT_SOURCE_DIR}/../../../conformance/generated)

add_executable(qc_test_psk test_psk.c)
target_link_libraries(qc_test_psk PRIVATE qonclave)
add_test(NAME psk COMMAND qc_test_psk)

add_executable(qc_test_checkin test_checkin.c)
target_link_libraries(qc_test_checkin PRIVATE qonclave)
target_include_directories(qc_test_checkin PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/..)
add_test(NAME checkin COMMAND qc_test_checkin)
