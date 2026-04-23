#include <Wire.h>

const int MPU_addr = 0x68; 
int16_t AcX, AcY, AcZ;

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22); // 強制指定腳位
  
  // 啟動 MPU6050
  Wire.beginTransmission(MPU_addr);
  Wire.write(0x6B); // 電源管理暫存器
  Wire.write(0);    // 喚醒晶片
  Wire.endTransmission(true);
  
  Serial.println("✅ 底層讀取啟動！");
}

void loop() {
  Wire.beginTransmission(MPU_addr);
  Wire.write(0x3B); // 從 X 軸加速度開始讀
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_addr, 6, true);
  
  AcX = Wire.read() << 8 | Wire.read();
  AcY = Wire.read() << 8 | Wire.read();
  AcZ = Wire.read() << 8 | Wire.read();

  // 轉換成約略的 G 力
  float x_g = AcX / 16384.0;
  float y_g = AcY / 16384.0;
  float z_g = AcZ / 16384.0;
  float total_g = sqrt(x_g*x_g + y_g*y_g + z_g*z_g);

  // 輸出給網頁用的格式: G,X,Y,Z
  Serial.print(total_g); Serial.print(",");
  Serial.print(x_g); Serial.print(",");
  Serial.print(y_g); Serial.print(",");
  Serial.println(z_g);

  delay(50);
}