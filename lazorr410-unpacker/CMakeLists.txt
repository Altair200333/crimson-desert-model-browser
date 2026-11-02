cmake_minimum_required(VERSION 3.15)
project(PAZ-Unpacker LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# --- Core library ---
add_library(paz STATIC
    lib/PamtFile.cpp
    lib/PamtExtractor.cpp
    lib/CryptChaCha20.cpp
    lib/Lz4.cpp
)
target_include_directories(paz PUBLIC lib)

if(MSVC)
    target_compile_options(paz PRIVATE /W3 /sdl)
    set_property(TARGET paz PROPERTY
        MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

# --- Native DLL for GUI P/Invoke ---
add_library(paz-native SHARED
    lib/paz_native.cpp
    lib/CryptChaCha20.cpp
    lib/Lz4.cpp
)
target_include_directories(paz-native PRIVATE lib)

if(MSVC)
    target_compile_options(paz-native PRIVATE /W3 /sdl)
    set_property(TARGET paz-native PROPERTY
        MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()
