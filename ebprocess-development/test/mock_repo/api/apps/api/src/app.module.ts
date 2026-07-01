import { Module } from "@nestjs/common";
import { MongooseModule } from "@nestjs/mongoose";
import { DataAccessModule } from "@app/data-access";

@Module({
  imports: [
    MongooseModule.forRoot(process.env.MONGO_URI || "mongodb://localhost/test"),
    DataAccessModule,
  ],
  controllers: [],
  providers: [],
})
export class AppModule {}
