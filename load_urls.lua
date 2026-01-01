-- load_urls.lua

counter = 0
paths = {}

-- Initialize the script: read URLs from url.txt
function init(args)
   local file = io.open("url.txt", "r")
   if not file then
      print("Error: could not open url.txt. Make sure it exists in the current directory.")
      return
   end

   for line in file:lines() do
      -- Trim whitespace if needed, though usually wrk handles paths fine. 
      -- But let's be safe and just take the line.
      -- If lines have comments or empty lines this might need logic, but url.txt seemed clean.
      if line ~= "" then
         table.insert(paths, line)
      end
   end
   file:close()
   
   if #paths == 0 then
      print("Warning: url.txt is empty!")
   end
end

-- Generate a request for each cycle
function request()
   counter = counter + 1
   -- Use modulo to cycle through paths
   -- Lua arrays are 1-based
   local index = (counter - 1) % #paths + 1
   local path = paths[index]
   
   return wrk.format("GET", path)
end

-- function delay()
--    return 1000
-- end
