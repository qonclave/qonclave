#
# Qonclave C SDK — implements the `constrained` and `minimal` profiles.
#
# Edge-only today. COMMUNICATION.md §3 explicitly invites "a new Compute Node in C++", so the
# layout anticipates a compute/ alongside edge/ rather than being named sdk/c-edge and having to
# move later.
#
# Deliberately boring CMake: this has to be consumable as an ESP-IDF component, a Zephyr module,
# and a plain add_subdirectory. Anything clever here becomes someone else's build failure.
#

cmake_minimum_required(VERSION 3.16)
project(qonclave C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

# --- options ------------------------------------------------------------------

# We ship exactly one port — posix — and only because the library has to be testable without
# hardware. Every other platform is the integrator's: a port is three functions against
# include/qonclave/port.h. Shipping empty stubs for boards we cannot build or test would be a
# rotting invitation to link against something that returns -1.
#
# Supply your own with:
#   cmake -S . -B build -DQONCLAVE_PORT_SOURCE=/path/to/my_port.c
set(QONCLAVE_PORT_SOURCE "" CACHE FILEPATH
    "Your platform port implementing include/qonclave/port.h. Empty uses the bundled posix port.")

# SHA-256 backend. `builtin` is bundled so host builds and CI need nothing installed; `mbedtls`
# reaches a hardware SHA peripheral where the platform has one, which on a sensor hashing every
# wake for five years is battery rather than a rounding error. ESP-IDF and Zephyr already ship
# mbedTLS, so on those targets it is not a new dependency.
set(QONCLAVE_SHA256_BACKEND "builtin" CACHE STRING "SHA-256 backend: builtin | mbedtls")
set_property(CACHE QONCLAVE_SHA256_BACKEND PROPERTY STRINGS builtin mbedtls)

# Escape hatch: your own implementation of the three streaming functions in src/sha256.h.
set(QONCLAVE_SHA256_SOURCE "" CACHE FILEPATH
    "Your own SHA-256 backend. Overrides QONCLAVE_SHA256_BACKEND.")

set(QONCLAVE_PROFILE "constrained" CACHE STRING "Conformance profile: constrained | minimal")
set_property(CACHE QONCLAVE_PROFILE PROPERTY STRINGS constrained minimal)

option(QONCLAVE_BUILD_TESTS "Build the tests. Requires the bundled posix port." ON)

# --- sources ------------------------------------------------------------------

set(QONCLAVE_SOURCES
    src/sha256_common.c
    src/cbor.c
    src/event.c
    src/command.c
    src/codec_cbor.c
    src/checkin.c
    src/placement.c
    src/psk.c
)

# Only the three streaming functions vary by backend. HMAC and the constant-time compare are in
# sha256_common.c so they exist once — a backend supplying its own HMAC would be a second
# implementation that could disagree about RFC 2104's oversized-key rule with nothing catching it.
if(QONCLAVE_SHA256_SOURCE)
    if(NOT EXISTS "${QONCLAVE_SHA256_SOURCE}")
        message(FATAL_ERROR "QONCLAVE_SHA256_SOURCE does not exist: ${QONCLAVE_SHA256_SOURCE}")
    endif()
    list(APPEND QONCLAVE_SOURCES "${QONCLAVE_SHA256_SOURCE}")
    set(QONCLAVE_SHA256_NAME "custom (${QONCLAVE_SHA256_SOURCE})")
elseif(QONCLAVE_SHA256_BACKEND STREQUAL "mbedtls")
    list(APPEND QONCLAVE_SOURCES src/sha256_mbedtls.c)
    set(QONCLAVE_SHA256_NAME "mbedtls")
elseif(QONCLAVE_SHA256_BACKEND STREQUAL "builtin")
    list(APPEND QONCLAVE_SOURCES src/sha256_builtin.c)
    set(QONCLAVE_SHA256_NAME "builtin")
else()
    message(FATAL_ERROR "Unknown QONCLAVE_SHA256_BACKEND '${QONCLAVE_SHA256_BACKEND}' (builtin | mbedtls)")
endif()

# The port supplies exactly three things: a network round trip, non-volatile storage for the spool
# and wake counter, and a monotonic clock. Keeping that surface to three functions is what makes a
# new platform a day of work rather than a fork.
if(QONCLAVE_PORT_SOURCE)
    if(NOT EXISTS "${QONCLAVE_PORT_SOURCE}")
        message(FATAL_ERROR "QONCLAVE_PORT_SOURCE does not exist: ${QONCLAVE_PORT_SOURCE}")
    endif()
    set(QONCLAVE_PORT_SOURCES "${QONCLAVE_PORT_SOURCE}")
    set(QONCLAVE_PORT_NAME "custom (${QONCLAVE_PORT_SOURCE})")

    # The tests drive a stand-in hub through a hook only the posix port has. Against a real port
    # they would have nothing to talk to, so refuse rather than build something that cannot pass.
    if(QONCLAVE_BUILD_TESTS)
        message(STATUS "Qonclave: custom port supplied, disabling tests (they need the posix port)")
        set(QONCLAVE_BUILD_TESTS OFF)
    endif()
else()
    set(QONCLAVE_PORT_SOURCES ports/posix/qc_port.c)
    set(QONCLAVE_PORT_NAME "posix (bundled, for tests and host builds)")
endif()

message(STATUS "Qonclave C SDK: profile=${QONCLAVE_PROFILE} port=${QONCLAVE_PORT_NAME} sha256=${QONCLAVE_SHA256_NAME}")

add_library(qonclave STATIC ${QONCLAVE_SOURCES} ${QONCLAVE_PORT_SOURCES})

if(QONCLAVE_SHA256_BACKEND STREQUAL "mbedtls" AND NOT QONCLAVE_SHA256_SOURCE)
    # Linked as a plain name rather than found via find_package: inside ESP-IDF and Zephyr the
    # target is provided by the build system already, and insisting on our own discovery would
    # break exactly the integrations this backend exists for.
    target_link_libraries(qonclave PUBLIC mbedcrypto)
endif()

target_include_directories(qonclave
    PUBLIC  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src
)

if(QONCLAVE_PROFILE STREQUAL "minimal")
    # The minimal profile is defined partly by what it cannot do. Compiling discovery out is not
    # an optimization — spec/v1/profiles/minimal.md forbids it, because an mDNS browse costs more
    # radio time than the device's entire useful exchange.
    target_compile_definitions(qonclave PUBLIC
        QC_PROFILE_MINIMAL=1
        QC_NO_DISCOVERY=1
        QC_CBOR_INT_KEYS=1
    )
else()
    target_compile_definitions(qonclave PUBLIC QC_PROFILE_CONSTRAINED=1)
endif()

if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(qonclave PRIVATE
        -Wall -Wextra -Wpedantic
        -Wconversion      # silent narrowing on a 32-bit MCU is a real source of field bugs
        -Wshadow
    )
elseif(MSVC)
    # MSVC rejects the GCC spellings outright, and the host build exists only so the library is
    # testable without hardware — the warnings that matter are the ones the cross-compiler emits.
    target_compile_options(qonclave PRIVATE /W4)
endif()

# --- tests --------------------------------------------------------------------

if(QONCLAVE_BUILD_TESTS)
    enable_testing()
    add_subdirectory(tests)
endif()

# --- install ------------------------------------------------------------------

install(TARGETS qonclave ARCHIVE DESTINATION lib)
install(DIRECTORY include/ DESTINATION include)
