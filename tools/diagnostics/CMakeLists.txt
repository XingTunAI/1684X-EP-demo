cmake_minimum_required(VERSION 3.13)
project(sophon_diagnostics LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

get_filename_component(DEMO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(SOPHON_DEMO_DIR "${DEMO_ROOT}/third_party/sophon-demo" CACHE PATH "Official demo repository")
set(YOLO_DIR "${SOPHON_DEMO_DIR}/sample/YOLOv8_plus_det/cpp/yolov8_bmcv")
set(DETECTOR_DIR "${DEMO_ROOT}/demos/yolov8/detector")
set(FFMPEG_DIR /opt/sophon/sophon-ffmpeg-latest/lib/cmake CACHE PATH "FFmpeg config")
set(OpenCV_DIR /opt/sophon/sophon-opencv-latest/lib/cmake/opencv4 CACHE PATH "OpenCV config")
find_package(FFMPEG REQUIRED)
find_package(OpenCV REQUIRED)
find_package(libsophon REQUIRED)
find_package(Threads REQUIRED)
include_directories(${CMAKE_CURRENT_SOURCE_DIR} ${DETECTOR_DIR} ${YOLO_DIR}
  ${FFMPEG_INCLUDE_DIRS} ${OpenCV_INCLUDE_DIRS} ${LIBSOPHON_INCLUDE_DIRS})
link_directories(${FFMPEG_LIB_DIRS} ${OpenCV_LIB_DIRS} ${LIBSOPHON_LIB_DIRS})

add_executable(inference_probe.pcie inference_probe.cpp)
target_compile_options(inference_probe.pcie PRIVATE -O2 -UNDEBUG)
target_link_libraries(inference_probe.pcie
  ${the_libbmlib.so} ${the_libbmrt.so} Threads::Threads)

add_executable(detector_check.pcie detector_check.cpp ${DETECTOR_DIR}/yolov8_det.cpp)
target_compile_options(detector_check.pcie PRIVATE -O2 -UNDEBUG)
target_link_libraries(detector_check.pcie ${FFMPEG_LIBS} ${OpenCV_LIBS}
  ${the_libbmlib.so} ${the_libbmrt.so} ${the_libbmcv.so} Threads::Threads)
