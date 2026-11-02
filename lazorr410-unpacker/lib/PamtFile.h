#pragma once

#include "PazTypes.h"
#include <string>
#include <vector>

namespace paz {

// Parse a .pamt index file and return file entries with resolved paths.
// The .pamt references one or more .paz data files (N.paz where N = 0,1,2,...).
// pazDir is the directory containing the .paz files (typically same dir as .pamt).
std::vector<FileEntry> parsePamtFile(const std::string &pamtPath,
                                      const std::string &pazDir);

} // namespace paz
