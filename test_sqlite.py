import sqlite3, os
d = r"C:\Users\baoxu\AppData\Local\fluid_scientist"
print("dir exists:", os.path.exists(d))
p = os.path.join(d, "test.db")
print("path:", p)
try:
    conn = sqlite3.connect(p)
    print("connected")
    conn.execute("CREATE TABLE t (x INTEGER)")
    print("table created")
    conn.close()
    print("done")
    os.remove(p)
except Exception as e:
    print("ERROR:", e)
    
# Try with TEMP
d2 = os.environ.get("TEMP", r"C:\Temp")
p2 = os.path.join(d2, "test_fs.db")
print(f"\nTEMP path: {p2}")
try:
    conn2 = sqlite3.connect(p2)
    print("connected")
    conn2.execute("CREATE TABLE t (x INTEGER)")
    print("table created")
    conn2.close()
    os.remove(p2)
    print("done")
except Exception as e:
    print("ERROR:", e)
