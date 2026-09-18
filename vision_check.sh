#!/bin/bash
# Send one image to a SambaNova model and print the raw API response.
#   ./vision_check.sh                  -> gemma-4-31B-it
#   ./vision_check.sh gpt-oss-120b     -> any other model
set -a; . "$(dirname "$0")/.env"; set +a

MODEL="${1:-gemma-4-31B-it}"

# 128x128 PNG, four coloured quadrants, generated inline so there is no asset to lose.
B64=$(python3 -c "
import struct, zlib, base64
def px(x,y):
    t,l = y<64, x<64
    return (220,30,30) if (t and l) else (30,170,60) if t else (40,70,210) if l else (240,210,40)
raw=b''.join(b'\x00'+b''.join(bytes(px(x,y)) for x in range(128)) for y in range(128))
def ch(tag,d):
    c=tag+d; return struct.pack('>I',len(d))+c+struct.pack('>I',zlib.crc32(c)&0xffffffff)
png=(b'\x89PNG\r\n\x1a\n'+ch(b'IHDR',struct.pack('>IIBBBBB',128,128,8,2,0,0,0))
     +ch(b'IDAT',zlib.compress(raw,9))+ch(b'IEND',b''))
print(base64.b64encode(png).decode())
")

python3 -c "
import json,sys
print(json.dumps({
  'model': sys.argv[1],
  'max_tokens': 60,
  'messages': [{'role':'user','content':[
      {'type':'text','text':'This image has four coloured quadrants. Name the colour of each: top-left, top-right, bottom-left, bottom-right.'},
      {'type':'image_url','image_url':{'url':'data:image/png;base64,'+sys.argv[2]}}]}]
}))" "$MODEL" "$B64" > /tmp/vision_payload.json

echo "model: $MODEL"
echo "---- raw response ----"
curl -s -w "\nHTTP %{http_code}\n" -X POST "$SAMBANOVA_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $SAMBANOVA_API_KEY" \
  -H "Content-Type: application/json" \
  --data @/tmp/vision_payload.json
